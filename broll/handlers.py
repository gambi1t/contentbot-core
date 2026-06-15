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
    BrollItem, BrollDraft, Status, SourceMode,
    save_draft, load_draft, new_draft_id, cleanup_expired,
)
from .materialize import materialize_items, validate_upload_media
from .source_menu import source_menu_keyboard
from bot_state import pending as _bot_pending, save_pending as _bot_save_pending

logger = logging.getLogger("broll.handlers")

# Durable-черновики Pipeline 2 (CTO-ревью Critical 1: переживают рестарт на
# длинных ветках). Отдельная папка, атомарная запись — см. broll.draft.
DRAFTS_DIR = Path(__file__).resolve().parent.parent / "broll_drafts"

# Фазовая выкатка источников. Проведены: AUTO, UPLOAD (Загрузить свои),
# MANUAL (Вручную из библиотеки), HF_ONLY (только графика). AUTO_HF (микс) —
# Фаза 3.
_ENABLED_MODES = (SourceMode.AUTO, SourceMode.UPLOAD, SourceMode.MANUAL,
                  SourceMode.HF_ONLY)

# State (в общем pending) для приёма загрузок / ручного выбора Pipeline 2.
_UPLOAD_STATE = "broll2_uploading"
_MANUAL_STATE = "broll2_manual"

# Подписи категорий для preview.
_SCENE_LABELS = {
    "karting": "картинг",
    "glamping": "глэмпинг",
    "sup": "SUP",
    "personal": "личное",
}


def _approval_keyboard(notion_url: str | None = None,
                       hf_draft_id: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("✅ Собрать ролик", callback_data="broll_approve")],
    ]
    # Только для HF-only (графика): ручная пересборка одной сцены (#14).
    if hf_draft_id:
        rows.append([InlineKeyboardButton(
            "🔁 Перегенерировать сцену", callback_data=f"b2hfre:{hf_draft_id}")])
    rows.append([InlineKeyboardButton("🔄 Другой сценарий", callback_data="broll_regen")])
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

    if mode == SourceMode.UPLOAD:
        up_dir = DRAFTS_DIR.parent / "broll_runs" / draft_id / "uploads"
        up_dir.mkdir(parents=True, exist_ok=True)
        draft.source_mode = SourceMode.UPLOAD
        draft.status = Status.UPLOADING
        draft.work_dir = str(up_dir)
        draft.source_items = []
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        # State в общий pending → process_photo/process_idea(video) роутят
        # загрузки сюда (handle_broll2_upload_message).
        uid = q.from_user.id
        _bot_pending[uid] = {"state": _UPLOAD_STATE, "broll2_draft_id": draft_id}
        _bot_save_pending(_bot_pending)
        await context.bot.send_message(
            chat_id,
            "📤 Пришли свои фото/видео (можно несколько). Когда закончишь — "
            "жми «✅ Готово».",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="b2up_done")],
                [InlineKeyboardButton("❌ Отмена", callback_data="b2up_cancel")],
            ]),
        )
        return

    if mode == SourceMode.MANUAL:
        from selfie.broll_picker import list_library_categories
        from .manual import manual_categories_keyboard
        cats = list_library_categories("video")
        if not cats:
            await context.bot.send_message(chat_id, "⚠️ Библиотека клипов пуста.")
            return
        draft.source_mode = SourceMode.MANUAL
        draft.status = Status.SELECTING_MANUAL
        draft.work_dir = str(DRAFTS_DIR.parent / "broll_runs" / draft_id)
        draft.source_items = []
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        _bot_pending[q.from_user.id] = {
            "state": _MANUAL_STATE, "broll2_draft_id": draft_id,
            "b2man_selected": [], "b2man_shown": [], "b2man_cat": None,
            "b2man_samples": [],
        }
        _bot_save_pending(_bot_pending)
        await context.bot.send_message(
            chat_id, "👆 Выбери категорию клипов:",
            reply_markup=manual_categories_keyboard(cats))
        return

    if mode == SourceMode.HF_ONLY:
        # Фаза 2: видеоряд целиком из HyperFrames. Отличие от AUTO ровно одно —
        # вместо select_clips зовём generate_hyperframes_broll; downstream
        # (items → materialize → превью → сборка) тот же.
        from hyperframes_broll import (
            generate_hyperframes_broll, HyperFramesInterrupted, HyperFramesTimeout,
        )
        from .draft import hf_items_from_clips

        work = DRAFTS_DIR.parent / "broll_runs" / draft_id
        work.mkdir(parents=True, exist_ok=True)
        draft.source_mode = SourceMode.HF_ONLY
        draft.status = Status.HF_RUNNING
        draft.work_dir = str(work)
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)

        header = "🎨 Графика (HyperFrames)"
        status = await context.bot.send_message(
            chat_id,
            f"{header}\n\nClaude пишет 6 сцен и рендерит их. Обычно 10-15 мин, "
            f"с доводкой вёрстки — до ~25. Шлю прогресс по шагам.",
        )
        # Прогресс-мост thread→Telegram (паттерн bot.py card_hfbroll): generate
        # крутится в to_thread, апдейты юзеру — через run_coroutine_threadsafe.
        # Fire-and-forget: сбой эдита («not modified») не валит генерацию.
        loop = asyncio.get_running_loop()
        _msg_id = status.message_id

        def _hf_progress(text: str) -> None:
            fut = asyncio.run_coroutine_threadsafe(
                context.bot.edit_message_text(
                    chat_id=chat_id, message_id=_msg_id,
                    text=f"{header}\n\n{text}"),
                loop,
            )
            fut.add_done_callback(lambda f: f.exception())

        def _hf_retry_kb() -> InlineKeyboardMarkup:
            return InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔄 Повторить графику",
                    callback_data=f"b2src:{SourceMode.HF_ONLY}:{draft_id}")],
                [InlineKeyboardButton(
                    "❌ Отмена", callback_data=f"b2src:cancel:{draft_id}")],
            ])

        # Чистим только старые hf_*.mp4 (namespace отдельный от autobroll/uploads).
        hf_dir = work / "hyperframes"
        if hf_dir.exists():
            for old in hf_dir.glob("hf_*.mp4"):
                try:
                    old.unlink()
                except OSError:
                    pass

        try:
            clips, _cost = await asyncio.to_thread(
                generate_hyperframes_broll, draft.script_text, work, _hf_progress)
        except HyperFramesInterrupted:
            await status.edit_text(
                "🔁 Графика не успела собраться — сервис перезапускался во время "
                "рендера. Сценарий на месте, жми «Повторить».",
                reply_markup=_hf_retry_kb())
            return
        except HyperFramesTimeout as e:
            await status.edit_text(
                f"⏱ {str(e)[:200]}\n\nГрафика не уложилась в лимит. Можно "
                f"повторить или сократить сценарий.",
                reply_markup=_hf_retry_kb())
            return
        except Exception as e:
            logger.error(f"[broll] HF-only generation failed: {e}", exc_info=True)
            await status.edit_text(
                f"⚠️ Не удалось сгенерировать графику: {str(e)[:200]}",
                reply_markup=_hf_retry_kb())
            return

        items = hf_items_from_clips(clips)
        if not items:
            await status.edit_text(
                "⚠️ Графика не сгенерировалась (0 сцен). Попробуй повторить.",
                reply_markup=_hf_retry_kb())
            return

        draft.source_items = items
        draft.status = Status.PREVIEW_READY
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        # hf_scene = passthrough mp4 (materialize не перекодирует) — единообразно
        # с другими источниками.
        clip_paths = await asyncio.to_thread(
            materialize_items, items, draft.work_dir)
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
            reply_markup=_approval_keyboard(draft.notion_url, hf_draft_id=draft_id),
            disable_web_page_preview=True,
        )
        return

    # Стале-callback на ещё-не-подключённый режим (Critical 3): не молчим.
    await context.bot.send_message(chat_id, "Этот режим скоро будет доступен.")


# ── #14: ручная пересборка одной HF-сцены ────────────────────────────────
async def _send_hf_preview(context, chat_id, draft, draft_id: str) -> None:
    """Заново показать превью HF-ролика (с кнопкой пересборки)."""
    clips = [str(it.path) for it in draft.source_items]
    context.user_data["broll_draft"] = {
        "script": draft.script_text, "clips": clips, "theme": draft.theme,
        "notion_url": draft.notion_url, "chat_id": draft.chat_id,
    }
    await context.bot.send_message(
        chat_id, _build_preview(draft.script_text, clips), parse_mode="HTML",
        reply_markup=_approval_keyboard(draft.notion_url, hf_draft_id=draft_id),
        disable_web_page_preview=True)


async def handle_hf_regen_menu(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               draft_id: str) -> None:
    """Шлёт 6 сцен-клипов с номерами + пикер «какую пересобрать» (callback b2hfre)."""
    q = update.callback_query
    chat_id = q.message.chat_id
    draft = load_draft(draft_id, DRAFTS_DIR)
    if draft is None or not draft.source_items:
        await context.bot.send_message(chat_id, "⚠️ Черновик устарел — запусти ролик заново.")
        return
    if not (Path(draft.work_dir) / "storyboard.json").is_file():
        await context.bot.send_message(
            chat_id, "⚠️ Пересборка доступна только для свежей графики — сделай "
            "новый ролик (🎨 Только графика).")
        return
    n = len(draft.source_items)
    await context.bot.send_message(chat_id, "Сцены по порядку — какую пересобрать?")
    for i, it in enumerate(draft.source_items, start=1):
        try:
            with open(it.path, "rb") as f:
                await context.bot.send_video(chat_id, f, caption=f"Сцена {i}")
        except Exception:
            await context.bot.send_message(chat_id, f"Сцена {i}: превью не отправилось")
    btns = [InlineKeyboardButton(str(i), callback_data=f"b2hfsc:{draft_id}:{i}")
            for i in range(1, n + 1)]
    rows = [btns[:3], btns[3:]] if n > 3 else [btns]
    rows.append([InlineKeyboardButton("⬅️ Назад к ролику", callback_data=f"b2hfback:{draft_id}")])
    await context.bot.send_message(
        chat_id, f"🔁 Номер сцены для пересборки (1–{n}):",
        reply_markup=InlineKeyboardMarkup(rows))


async def handle_hf_regen_scene(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                draft_id: str, n: int) -> None:
    """Пересобрать сцену N (callback b2hfsc:<draft_id>:<n>)."""
    import json as _json
    from hyperframes_broll import regenerate_scene
    q = update.callback_query
    chat_id = q.message.chat_id
    draft = load_draft(draft_id, DRAFTS_DIR)
    if draft is None or not draft.source_items:
        await context.bot.send_message(chat_id, "⚠️ Черновик устарел.")
        return
    sb_path = Path(draft.work_dir) / "storyboard.json"
    if not sb_path.is_file():
        await context.bot.send_message(chat_id, "⚠️ Раскадровка не найдена — сделай новый ролик.")
        return
    if not (1 <= n <= len(draft.source_items)):
        await context.bot.send_message(chat_id, "⚠️ Нет такой сцены.")
        return
    try:
        storyboard = _json.loads(sb_path.read_text(encoding="utf-8"))
    except Exception:
        await context.bot.send_message(chat_id, "⚠️ Раскадровка повреждена — сделай новый ролик.")
        return
    status = await context.bot.send_message(chat_id, f"🔁 Пересобираю сцену {n} (~1-2 мин)…")
    mp4 = await asyncio.to_thread(
        regenerate_scene, storyboard, f"scene_{n:02d}", Path(draft.work_dir))
    if not mp4:
        await status.edit_text(
            f"⚠️ Сцену {n} пересобрать не вышло — старая осталась. Можно повторить.")
        return
    try:
        await status.delete()
    except Exception:
        pass
    try:
        with open(mp4, "rb") as f:
            await context.bot.send_video(chat_id, f, caption=f"✅ Сцена {n} пересобрана")
    except Exception:
        pass
    # путь клипа не изменился (hf_NN.mp4 перезаписан) — заново показываем превью
    await _send_hf_preview(context, chat_id, draft, draft_id)


async def handle_hf_regen_back(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               draft_id: str) -> None:
    """Назад к превью HF-ролика (callback b2hfback)."""
    chat_id = update.callback_query.message.chat_id
    draft = load_draft(draft_id, DRAFTS_DIR)
    if draft is None or not draft.source_items:
        await context.bot.send_message(chat_id, "⚠️ Черновик устарел — запусти ролик заново.")
        return
    await _send_hf_preview(context, chat_id, draft, draft_id)


async def handle_broll2_manual_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Диспетчер ручного пикера (callback b2man:*). Мультивыбор из библиотеки."""
    import library_manager
    from selfie.broll_picker import (
        list_library_sample, list_library_categories, lookup_library_path)
    from .manual import (
        parse_b2man_cb, manual_toggle_keyboard, manual_categories_keyboard,
        manual_items_from_ids)

    q = update.callback_query
    uid = q.from_user.id
    chat_id = q.message.chat_id
    d = _bot_pending.get(uid) or {}
    if d.get("state") != _MANUAL_STATE:
        await q.answer("Кнопка устарела", show_alert=True)
        return
    action, cat, item_id = parse_b2man_cb(q.data)
    selected = set(d.get("b2man_selected", []))
    await q.answer()

    if action == "cats":
        cats = list_library_categories("video")
        await q.edit_message_text("👆 Выбери категорию клипов:",
                                  reply_markup=manual_categories_keyboard(cats))
        return

    if action in ("cat", "reroll"):
        the_cat = cat or d.get("b2man_cat")
        exclude = d.get("b2man_shown", []) if action == "reroll" else []
        samples = list_library_sample("video", the_cat, 6, exclude)
        if not samples:
            await context.bot.send_message(chat_id, "Больше клипов в категории нет.")
            cats = list_library_categories("video")
            await context.bot.send_message(
                chat_id, "👆 Выбери категорию:",
                reply_markup=manual_categories_keyboard(cats))
            return
        d["b2man_cat"] = the_cat
        d["b2man_shown"] = (d.get("b2man_shown", []) if action == "reroll" else []) \
            + [s["id"] for s in samples]
        d["b2man_samples"] = samples
        _bot_pending[uid] = d
        _bot_save_pending(_bot_pending)
        await library_manager._send_previews(context, chat_id, samples, "video")
        await context.bot.send_message(
            chat_id, "Отметь нужные клипы (✅), потом «Готово»:",
            reply_markup=manual_toggle_keyboard(samples, the_cat, selected, len(selected)))
        return

    if action == "tog":
        if item_id in selected:
            selected.discard(item_id)
        else:
            selected.add(item_id)
        d["b2man_selected"] = list(selected)
        _bot_pending[uid] = d
        _bot_save_pending(_bot_pending)
        try:
            await q.edit_message_reply_markup(reply_markup=manual_toggle_keyboard(
                d.get("b2man_samples", []), d.get("b2man_cat", ""), selected, len(selected)))
        except Exception:
            pass
        return

    if action == "done":
        if not selected:
            await context.bot.send_message(chat_id, "Ничего не выбрано — отметь хотя бы один клип.")
            return
        items = manual_items_from_ids(list(selected), lookup_library_path)
        draft = load_draft(d.get("broll2_draft_id"), DRAFTS_DIR)
        if draft is None or not items:
            await context.bot.send_message(chat_id, "⚠️ Черновик потерян — запусти заново.")
            _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
            return
        draft.source_items = items
        draft.status = Status.PREVIEW_READY
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
        # Библиотечные видео = passthrough (materialize не меняет mp4, единообразно).
        clip_paths = await asyncio.to_thread(
            materialize_items, items, draft.work_dir or str(DRAFTS_DIR.parent))
        context.user_data["broll_draft"] = {
            "script": draft.script_text,
            "clips": [str(p) for p in clip_paths],
            "theme": draft.theme,
            "notion_url": draft.notion_url,
            "chat_id": draft.chat_id,
        }
        try:
            await q.edit_message_reply_markup(reply_markup=None)
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


async def handle_broll2_upload_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Приём фото/видео в state broll2_uploading. True если обработано.

    Скачивает файл → validate_upload_media → кладёт в draft.work_dir →
    добавляет BrollItem(origin=upload) в durable-draft. Битый/невалидный —
    отклоняет с причиной, не добавляет."""
    uid = update.effective_user.id
    d = _bot_pending.get(uid) or {}
    if d.get("state") != _UPLOAD_STATE:
        return False
    draft_id = d.get("broll2_draft_id")
    draft = load_draft(draft_id, DRAFTS_DIR) if draft_id else None
    if draft is None:
        await update.message.reply_text("⚠️ Черновик потерян — запусти B-roll ролик заново.")
        _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
        return True

    msg = update.message
    work = Path(draft.work_dir); work.mkdir(parents=True, exist_ok=True)
    n = len(draft.source_items)
    if msg.photo:
        kind = "image"
        tg_file = await context.bot.get_file(msg.photo[-1].file_id)
        dest = work / f"up_{n + 1:03d}.jpg"
    else:
        vid = msg.video or msg.document
        if not vid:
            return False
        kind = "video"
        suffix = ".mp4"
        dest = work / f"up_{n + 1:03d}{suffix}"
        tg_file = await context.bot.get_file(vid.file_id)
    await tg_file.download_to_drive(str(dest))

    ok, reason = validate_upload_media(dest, kind)
    if not ok:
        try:
            dest.unlink()
        except OSError:
            pass
        await msg.reply_text(f"⚠️ Файл не подошёл: {reason}")
        return True

    draft.source_items.append(BrollItem(
        kind=kind, origin="upload", path=str(dest),
        label=f"upload/{dest.name}"))
    draft.touch(time.time())
    save_draft(draft, DRAFTS_DIR)
    # Кнопки на КАЖДОМ ack (Telethon 13 июня): иначе «Готово» уезжает вверх
    # на первом сообщении и недоступно после загрузок.
    await msg.reply_text(
        f"✅ Добавлено ({len(draft.source_items)}). Ещё файл или жми «Готово».",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Готово", callback_data="b2up_done")],
            [InlineKeyboardButton("❌ Отмена", callback_data="b2up_cancel")],
        ]),
    )
    return True


async def finish_broll_upload(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              claude=None) -> None:
    """«✅ Готово» загрузки: materialize items → превью → существующая сборка."""
    q = update.callback_query
    uid = q.from_user.id
    chat_id = q.message.chat_id
    d = _bot_pending.get(uid) or {}
    draft = load_draft(d.get("broll2_draft_id"), DRAFTS_DIR) if d.get("broll2_draft_id") else None
    if draft is None or not draft.source_items:
        await context.bot.send_message(chat_id, "⚠️ Ничего не загружено — пришли фото/видео.")
        return
    status = await context.bot.send_message(chat_id, "🎬 Обрабатываю загруженное…")
    # Фото → Ken Burns mp4, видео → passthrough (materialize, тестировано).
    clip_paths = await asyncio.to_thread(
        materialize_items, draft.source_items, draft.work_dir)
    if not clip_paths:
        await status.edit_text("⚠️ Ни один файл не удалось подготовить. Попробуй другие.")
        return
    draft.status = Status.PREVIEW_READY
    draft.touch(time.time())
    save_draft(draft, DRAFTS_DIR)
    _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
    # Переиспользуем существующую сборку через dict-контракт.
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


async def cancel_broll_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«❌ Отмена» загрузки: чистим state + черновик."""
    q = update.callback_query
    uid = q.from_user.id
    d = _bot_pending.get(uid) or {}
    did = d.get("broll2_draft_id")
    if did:
        try:
            (DRAFTS_DIR / f"{did}.json").unlink()
        except OSError:
            pass
    _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
    await context.bot.send_message(q.message.chat_id, "✖️ Загрузка отменена.")


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
    "handle_broll2_manual_cb",
    "handle_broll2_upload_message",
    "finish_broll_upload",
    "cancel_broll_upload",
    "assemble_broll_from_draft",
    "regenerate_broll_preview",
    "cancel_broll",
]
