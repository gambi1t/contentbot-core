"""Telegram-хендлеры B-roll монтажа (Pipeline #2) — 2-фазный flow.

Фаза 1 (preview):  тема → Claude пишет закадровый сценарий → selector
                   выбирает клипы → текстовый preview + кнопки.
Фаза 2 (assemble): approve → озвучка голосом Максима → ffmpeg-монтаж →
                   субтитры → отправка MP4.

Озвучка и тяжёлый ffmpeg запускаются ТОЛЬКО после approve — на preview
ElevenLabs-кредиты и CPU не тратятся.

Черновик хранится в `context.user_data["broll_draft"]` между фазами
(in-memory, теряется при рестарте бота — для коротких preview-окон ок).

Callbacks (регистрируются в bot.py handle_callback):
    broll_approve → assemble_broll_from_draft (фаза 2)
    broll_regen   → regenerate_broll_preview  (фаза 1 заново)
    broll_cancel  → cancel_broll
"""
from __future__ import annotations

import asyncio
import html as html_mod
import logging
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .assembler import MontageError, assemble_broll_montage
from .llm import generate_script
from .selector import SelectorError, select_clips
from .draft import (
    BrollDraft, Status, SourceMode,
    save_draft, load_draft, new_draft_id, cleanup_expired,
)
from .source_menu import source_menu_keyboard

logger = logging.getLogger("broll.handlers")

# Durable-черновики Pipeline 2 (CTO-ревью Critical 1: переживают рестарт на
# длинных ветках). Отдельная папка, атомарная запись — см. broll.draft.
DRAFTS_DIR = Path(__file__).resolve().parent.parent / "broll_drafts"

# Фазовая выкатка источников: пока проведён только AUTO (Авто из библиотеки).
# Вручную/Загрузить/HF добавляются следующими инкрементами.
_ENABLED_MODES = (SourceMode.AUTO,)

# Подписи категорий для preview.
_SCENE_LABELS = {
    "karting": "картинг",
    "glamping": "глэмпинг",
    "sup": "SUP",
    "personal": "личное",
}


def _approval_keyboard(notion_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("✅ Собрать ролик", callback_data="broll_approve")],
        [InlineKeyboardButton("🔄 Другой сценарий", callback_data="broll_regen")],
    ]
    if notion_url:
        rows.append([InlineKeyboardButton("📋 К карточке", url=notion_url)])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="broll_cancel")])
    return InlineKeyboardMarkup(rows)


def _build_preview(script: str, clip_paths: list[str]) -> str:
    """Текст preview: сценарий + сводка видеоряда."""
    esc = lambda s: html_mod.escape(s or "", quote=False)  # noqa: E731
    by_scene = Counter(Path(p).parent.name for p in clip_paths)
    breakdown = ", ".join(
        f"{_SCENE_LABELS.get(scene, scene)} ×{n}"
        for scene, n in by_scene.most_common()
    )
    return (
        f"🎞 <b>B-roll ролик</b> — закадровый голос + видеоряд, без аватара\n\n"
        f"<b>Сценарий (озвучка голосом Максима):</b>\n"
        f"<i>{esc(script)}</i>\n\n"
        f"🎬 <b>Видеоряд:</b> {len(clip_paths)} клипов — {esc(breakdown)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Жми <b>«Собрать ролик»</b> — бот озвучит сценарий, смонтирует "
        f"видеоряд и наложит субтитры (~1-3 мин). <b>«Другой сценарий»</b> — "
        f"переписать заново."
    )


async def generate_broll_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    claude,
    theme: str,
    chat_id: int | None = None,
    notion_url: str | None = None,
) -> None:
    """Фаза 1: сгенерить сценарий + выбрать клипы, показать preview.

    Черновик кладётся в `context.user_data["broll_draft"]` для фазы 2.
    """
    if chat_id is None:
        q = update.callback_query
        chat_id = q.message.chat_id if q else update.effective_chat.id

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎞 Готовлю B-roll ролик\n"
            f"<i>Тема:</i> {html_mod.escape(theme[:140])}\n\n"
            f"⏳ Пишу закадровый сценарий и подбираю видеоряд…"
        ),
        parse_mode="HTML",
    )

    # Сценарий
    try:
        script = await asyncio.to_thread(generate_script, claude, theme)
    except Exception as e:
        logger.error(f"[broll] script generation failed: {e}", exc_info=True)
        try:
            await status.edit_text(f"❌ Не получилось написать сценарий: {e}\n\nПопробуй ещё раз.")
        except Exception:
            pass
        return

    # Durable-черновик (CTO-ревью Critical 1): создаём сразу после сценария —
    # переживает рестарт, пока юзер выбирает источник видеоряда. Выбор клипов
    # перенесён в обработчик режима (Авто), чтобы не тратить его, если юзер
    # выберет ручной/загрузку/графику.
    cleanup_expired(DRAFTS_DIR, now=time.time())
    uid = (update.effective_user.id if update.effective_user else
           (update.callback_query.from_user.id if update.callback_query else 0))
    now = time.time()
    draft = BrollDraft(
        draft_id=new_draft_id(uid, now), user_id=uid, chat_id=chat_id,
        status=Status.AWAITING_SOURCE, source_mode=None,
        script_text=script, voice_estimate_sec=0.0, source_items=[],
        work_dir="", notion_url=notion_url, theme=theme,
        created_at=now, updated_at=now,
    )
    save_draft(draft, DRAFTS_DIR)
    context.user_data["broll_draft_id"] = draft.draft_id

    try:
        await status.delete()
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎞 <b>B-roll ролик</b> — закадровый голос + видеоряд, без аватара\n\n"
            f"<b>Сценарий (озвучка голосом Максима):</b>\n"
            f"<i>{html_mod.escape(script)}</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\nОткуда взять видеоряд?"
        ),
        parse_mode="HTML",
        reply_markup=source_menu_keyboard(draft.draft_id, enabled_modes=_ENABLED_MODES),
        disable_web_page_preview=True,
    )


async def handle_broll_source(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    claude,
    draft_id: str,
    mode: str,
) -> None:
    """Обработка выбора источника видеоряда (callback b2src:<mode>:<draft_id>).

    Этот инкремент: AUTO (Авто из библиотеки) + cancel. Остальные режимы
    подключаются следующими инкрементами (в меню они пока скрыты)."""
    q = update.callback_query
    chat_id = q.message.chat_id
    draft = load_draft(draft_id, DRAFTS_DIR)
    if draft is None:
        await context.bot.send_message(
            chat_id, "⚠️ Черновик устарел или потерян — запусти B-roll ролик заново.")
        return

    if mode == "cancel":
        try:
            (DRAFTS_DIR / f"{draft_id}.json").unlink()
        except OSError:
            pass
        await context.bot.send_message(chat_id, "✖️ B-roll ролик отменён.")
        return

    if mode == SourceMode.AUTO:
        status = await context.bot.send_message(chat_id, "🤖 Подбираю клипы под сценарий…")
        try:
            clip_paths = await asyncio.to_thread(select_clips, draft.script_text, claude)
        except SelectorError as e:
            logger.error(f"[broll] auto clip selection failed: {e}")
            await status.edit_text(f"❌ Архив B-roll клипов недоступен.\n\n<code>{e}</code>",
                                   parse_mode="HTML")
            return
        except Exception as e:
            logger.error(f"[broll] auto clip selection failed: {e}", exc_info=True)
            await status.edit_text(f"❌ Не получилось подобрать клипы: {e}")
            return

        draft.source_mode = SourceMode.AUTO
        draft.status = Status.PREVIEW_READY
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        # Переиспользуем существующую сборку (Фаза 2) через её dict-контракт —
        # для AUTO видеоряд = только видео, materialize не нужен (passthrough).
        context.user_data["broll_draft"] = {
            "script": draft.script_text,
            "clips": [str(p) for p in clip_paths],
            "theme": draft.theme,
            "notion_url": draft.notion_url,
            "chat_id": draft.chat_id,
        }
        try:
            await status.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=_build_preview(draft.script_text, [str(p) for p in clip_paths]),
            parse_mode="HTML",
            reply_markup=_approval_keyboard(draft.notion_url),
            disable_web_page_preview=True,
        )
        return

    # Стале-callback на ещё-не-подключённый режим (Critical 3): не молчим.
    await context.bot.send_message(chat_id, "Этот режим скоро будет доступен.")


async def assemble_broll_from_draft(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    voiceover_fn,
    chat_id: int | None = None,
) -> None:
    """Фаза 2: озвучка → ffmpeg-монтаж → субтитры → отправка MP4.

    voiceover_fn — функция generate_voiceover(text, out_path) из bot.py
    (передаётся параметром, чтобы не плодить циклический импорт).
    """
    draft = context.user_data.get("broll_draft")
    if not draft:
        await context.bot.send_message(
            chat_id=chat_id or update.effective_chat.id,
            text="⚠️ Черновик потерян (бот мог рестартнуть). Запусти B-roll ролик заново.",
        )
        return

    script = draft["script"]
    clip_paths = [Path(p) for p in draft["clips"]]
    notion_url = draft.get("notion_url")
    if chat_id is None:
        chat_id = draft.get("chat_id") or update.effective_chat.id

    status = await context.bot.send_message(
        chat_id=chat_id,
        text="🎬 Озвучиваю сценарий и собираю ролик…\n<i>~1-3 минуты</i>",
        parse_mode="HTML",
    )

    work_dir = Path(tempfile.mkdtemp(prefix=f"broll_{chat_id}_"))
    try:
        # 1. Озвучка голосом Максима
        voice_path = work_dir / "voiceover.mp3"
        try:
            await asyncio.to_thread(voiceover_fn, script, str(voice_path))
        except Exception as e:
            logger.error(f"[broll] voiceover failed: {e}", exc_info=True)
            await status.edit_text(f"❌ Озвучка не получилась: {e}")
            return
        if not voice_path.exists() or voice_path.stat().st_size < 1000:
            await status.edit_text("❌ Озвучка вернула пустой файл.")
            return

        # 2. ffmpeg-монтаж
        try:
            await status.edit_text("🎬 Монтирую видеоряд под озвучку…")
        except Exception:
            pass
        montage_path = work_dir / "montage.mp4"
        try:
            await asyncio.to_thread(
                assemble_broll_montage, clip_paths, voice_path, montage_path, work_dir,
            )
        except MontageError as e:
            logger.error(f"[broll] montage failed: {e}", exc_info=True)
            await status.edit_text(f"❌ Сборка монтажа упала: {e}")
            return

        # 3. Субтитры (graceful — при сбое отдаём ролик без субтитров)
        try:
            await status.edit_text("📝 Накладываю субтитры…")
        except Exception:
            pass
        final_path = montage_path
        try:
            from subtitle_burner import add_subtitles_to_video
            subbed = await asyncio.to_thread(
                add_subtitles_to_video,
                montage_path,
                voice_path,                       # audio_path — точная транскрипция
                work_dir / "montage_subbed.mp4",  # output_path
            )
            final_path = Path(subbed)
        except Exception as e:
            logger.warning(f"[broll] subtitles failed (non-fatal): {e}")

        # 4. Отправка
        try:
            await status.delete()
        except Exception:
            pass
        caption = (
            f"✅ <b>B-roll ролик готов</b>\n\n"
            f"Закадровый голос Максима + видеоряд из архива + субтитры. "
            f"Без аватара.\n\n"
            f"<i>Можно публиковать в Reels / TikTok / Shorts.</i>"
        )
        with open(final_path, "rb") as vf:
            await context.bot.send_video(
                chat_id=chat_id,
                video=vf,
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True,
            )

        action_rows = [[InlineKeyboardButton("🔄 Ещё B-roll ролик", callback_data="broll_regen")]]
        if notion_url:
            action_rows.append([InlineKeyboardButton("📋 К карточке", url=notion_url)])
        action_rows.append([InlineKeyboardButton("◀️ В главное меню", callback_data="idea_back_to_menu")])
        await context.bot.send_message(
            chat_id=chat_id,
            text="Готово. Что дальше?",
            reply_markup=InlineKeyboardMarkup(action_rows),
        )

        context.user_data.pop("broll_draft", None)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def regenerate_broll_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    claude,
) -> None:
    """Перегенерить preview с той же темой (callback broll_regen)."""
    draft = context.user_data.get("broll_draft")
    if not draft:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Черновик не найден — запусти B-roll ролик заново.",
        )
        return
    await generate_broll_preview(
        update, context, claude,
        theme=draft["theme"],
        chat_id=draft.get("chat_id"),
        notion_url=draft.get("notion_url"),
    )


async def cancel_broll(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Сбросить черновик (callback broll_cancel)."""
    context.user_data.pop("broll_draft", None)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✖️ B-roll ролик отменён.",
    )


__all__ = [
    "generate_broll_preview",
    "handle_broll_source",
    "assemble_broll_from_draft",
    "regenerate_broll_preview",
    "cancel_broll",
]
