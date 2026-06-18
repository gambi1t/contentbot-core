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
from .source_menu import source_menu_keyboard, hf_fallback_action
from bot_state import pending as _bot_pending, save_pending as _bot_save_pending
from music_mixer import list_categories, pick_random_track
from selfie.cover import (
    extract_frame, get_frame_timestamps, probe_video_duration,
    list_library_sample, lookup_library_path,
)

logger = logging.getLogger("broll.handlers")

# Durable-черновики Pipeline 2 (CTO-ревью Critical 1: переживают рестарт на
# длинных ветках). Отдельная папка, атомарная запись — см. broll.draft.
DRAFTS_DIR = Path(__file__).resolve().parent.parent / "broll_drafts"

# Фазовая выкатка источников. Проведены: AUTO, UPLOAD (Загрузить свои),
# MANUAL (Вручную из библиотеки), HF_ONLY (только графика). AUTO_HF (микс) —
# Фаза 3.
_ENABLED_MODES = (SourceMode.AUTO, SourceMode.UPLOAD, SourceMode.MANUAL,
                  SourceMode.HF_ONLY, SourceMode.AUTO_HF, SourceMode.AI_VIDEO)

# State (в общем pending) для приёма загрузок / ручного выбора Pipeline 2.
_UPLOAD_STATE = "broll2_uploading"
_MANUAL_STATE = "broll2_manual"
# Инкремент 1: правка сценария свободным текстом (гейт до меню источника).
_EDIT_SCRIPT_STATE = "broll2_edit_script"
# Инкремент 4: гейт обложки (пост-сборка). Состояния: ввод текста / приём фото.
_COVER_TEXT_STATE = "broll2_cover_text"
_COVER_UPLOAD_STATE = "broll2_cover_upload"


def _broll_final_path(uid: int) -> Path:
    """Per-user стабильный путь собранного монтажа (переживает rmtree work_dir,
    нужен пост-сборочному гейту обложки для кадра). 1 файл/юзер, перезапись,
    удаляется после готовой обложки — не копится (как ai_voice/music)."""
    d = DRAFTS_DIR.parent / "broll_finals"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{uid}.mp4"

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


def _script_gate_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """Гейт #1 (инкремент 1): сценарий написан — править или утвердить.
    Стоит ДО меню источника видеоряда. Отмена реюзит общий broll_cancel
    (cancel_broll безопасен без dict-черновика). «Другой сценарий» сюда НЕ
    кладём: regenerate_broll_preview читает context.user_data['broll_draft']
    (dict), которого на этом шаге ещё нет — упадёт."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Править сценарий", callback_data=f"b2scr:edit:{draft_id}")],
        [InlineKeyboardButton("✅ Утвердить сценарий", callback_data=f"b2scr:ok:{draft_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="broll_cancel")],
    ])


def _script_editing_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """Во время правки: единственная кнопка — выйти обратно на гейт."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Отмена правки", callback_data=f"b2scr:cancel_edit:{draft_id}")],
    ])


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


def _interleave(primary: list, secondary: list) -> list:
    """Чередует два списка [p0,s0,p1,s1,…]; более длинный хвост идёт в конец.
    Фаза 3 (Авто+графика): графика + живое видео по очереди. Монтаж берёт
    сбалансированный префикс под длину озвучки (assemble_broll_montage idx%len)."""
    out = []
    for i in range(max(len(primary), len(secondary))):
        if i < len(primary):
            out.append(primary[i])
        if i < len(secondary):
            out.append(secondary[i])
    return out


def _broll_card_data(theme: str) -> dict:
    """card_data для create_notion_card из темы B-roll (своя идея → карточка
    на Kanban). Формат — короткое видео; рубрика/площадки/призыв — дефолты бренда."""
    return {
        "title": (theme or "B-roll ролик").strip()[:80],
        "format": ["Short video"],
        "cta": "",
    }


async def generate_broll_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    claude,
    theme: str,
    chat_id: int | None = None,
    notion_url: str | None = None,
    notion_card_fn=None,
) -> None:
    """Фаза 1: сгенерить сценарий + выбрать клипы, показать preview.

    Черновик кладётся в `context.user_data["broll_draft"]` для фазы 2.
    notion_card_fn — create_notion_card (передаётся из bot.py, т.к. обратный
    импорт нельзя): если карточки ещё нет (своя идея, notion_url пуст) — создаём
    её со сценарием → идея попадает на Kanban SMM-менеджера.
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
    # Своя идея (notion_url пуст) → создаём карточку Notion со сценарием, чтобы
    # идея попала на Kanban SMM. Из банка идей карточка уже есть — не дублируем.
    notion_page_id = None
    if not notion_url and notion_card_fn:
        try:
            notion_url, notion_page_id = await asyncio.to_thread(
                notion_card_fn, _broll_card_data(theme), script)
        except Exception as e:
            logger.warning(f"[broll] не создал Notion-карточку: {e}")
    draft = BrollDraft(
        draft_id=new_draft_id(uid, now), user_id=uid, chat_id=chat_id,
        status=Status.AWAITING_SOURCE, source_mode=None,
        script_text=script, voice_estimate_sec=0.0, source_items=[],
        work_dir="", notion_url=notion_url, notion_page_id=notion_page_id,
        theme=theme, created_at=now, updated_at=now,
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
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Проверь сценарий: <b>«✏️ Править»</b> — поправить текст, "
            f"<b>«✅ Утвердить»</b> — перейти к выбору видеоряда."
        ),
        parse_mode="HTML",
        reply_markup=_script_gate_keyboard(draft.draft_id),
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
            "notion_page_id": draft.notion_page_id,
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
        # Фаза 2: видеоряд целиком из HyperFrames.
        from .draft import hf_items_from_clips
        clips = await _generate_hf_clips(context, chat_id, draft, draft_id,
                                         SourceMode.HF_ONLY)
        if clips is None:
            return
        items = hf_items_from_clips(clips)
        if not items:
            await context.bot.send_message(
                chat_id, "⚠️ Графика не сгенерировалась. Попробуй повторить.")
            return
        draft.source_items = items
        draft.status = Status.PREVIEW_READY
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        await asyncio.to_thread(materialize_items, items, draft.work_dir)
        await _send_hf_preview(context, chat_id, draft, draft_id, with_clips=True)
        return

    if mode == SourceMode.AUTO_HF:
        # Фаза 3: МИКС — графика HyperFrames + живые клипы библиотеки, чередуются.
        # Монтаж берёт сбалансированный префикс под длину озвучки. Графика тут
        # ОПЦИОНАЛЬНА: если она сорвалась, а библиотека есть → собираем live_only.
        from .draft import hf_items_from_clips
        clips = await _generate_hf_clips(context, chat_id, draft, draft_id,
                                         SourceMode.AUTO_HF, allow_live_fallback=True)
        hf_items = hf_items_from_clips(clips) if clips else []
        # Библиотечные клипы (реюз AUTO).
        try:
            lib_paths = await asyncio.to_thread(select_clips, draft.script_text, claude)
        except Exception as e:
            logger.warning(f"[broll] AUTO_HF select_clips: {e}")
            lib_paths = []
        lib_items = [BrollItem(kind="video", origin="auto", path=str(p),
                               label=f"auto/{Path(p).name}") for p in lib_paths]
        # Политика на провал графики (CTO-ревью Q7, hf_fallback_action).
        action = hf_fallback_action(SourceMode.AUTO_HF, hf_ok_count=len(hf_items),
                                    live_available=bool(lib_items))
        if action == "fail":
            await context.bot.send_message(
                chat_id, "⚠️ Не из чего собрать — ни графики, ни клипов в библиотеке.")
            return
        if action == "live_only":
            await context.bot.send_message(
                chat_id, "⚠️ Графика не собралась — делаю ролик из живых клипов.")
        items = _interleave(hf_items, lib_items)
        draft.source_items = items
        draft.status = Status.PREVIEW_READY
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        await asyncio.to_thread(materialize_items, items, draft.work_dir)
        # Микс: пер-сценная пересборка не применима (позиции ≠ scene_NN) → regen=False.
        await _send_hf_preview(context, chat_id, draft, draft_id,
                               with_clips=True, regen=False)
        return

    if mode == SourceMode.AI_VIDEO:
        # Фуллскрин Seedance. Экран подтверждения: число клипов под длину озвучки
        # (оценка из слов) + цена → запуск (b2src:ai_video_go). Без зацикливания —
        # генерим с запасом, ассемблер подрежет последний.
        import ai_video_broll
        plan = ai_video_broll.fullscreen_plan(draft.script_text)
        await q.edit_message_text(
            f"🎬 AI-видео по сценарию (Seedance)\n\n"
            f"Сценарий ~{plan['est_sec']:.0f}с → сгенерирую {plan['n_clips']} клипов "
            f"по {plan['clip_len']}с (~${plan['cost']:.2f}).\n"
            f"Голос (свой/AI) + субтитры + музыка — как обычно.\n\n"
            f"Несколько минут на генерацию. Запустить?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"🚀 Запустить ({plan['n_clips']} клипов ~${plan['cost']:.2f})",
                    callback_data=f"b2src:{SourceMode.AI_VIDEO_GO}:{draft_id}")],
                [InlineKeyboardButton(
                    "⬅️ К источникам",
                    callback_data=f"b2src:{SourceMode.AI_VIDEO_MENU}:{draft_id}")],
            ]),
        )
        return

    if mode == SourceMode.AI_VIDEO_MENU:
        # «Назад к источникам» с экрана подтверждения — перерисовать меню.
        await q.edit_message_text(
            "Откуда взять видеоряд?",
            reply_markup=source_menu_keyboard(draft_id, enabled_modes=_ENABLED_MODES),
        )
        return

    if mode == SourceMode.AI_VIDEO_GO:
        # Подтверждено — генерируем фуллскрин AI-видео (N клипов под длину).
        from .draft import hf_items_from_clips
        import ai_video_broll
        plan = ai_video_broll.fullscreen_plan(draft.script_text)
        work = DRAFTS_DIR.parent / "broll_runs" / draft_id
        work.mkdir(parents=True, exist_ok=True)
        draft.source_mode = SourceMode.AI_VIDEO
        draft.status = Status.HF_RUNNING
        draft.work_dir = str(work)
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        status = await context.bot.send_message(
            chat_id, f"🎬 Генерирую AI-видео (Seedance): {plan['n_clips']} клипов по "
                     f"{plan['clip_len']}с… Несколько минут.")
        try:
            clips, _cost = await asyncio.to_thread(
                ai_video_broll.generate_ai_broll, draft.script_text, work,
                duration=plan["clip_len"], target_clips=plan["n_clips"])
        except Exception as e:
            logger.error(f"[broll] ai_video failed: {e}", exc_info=True)
            await status.edit_text(f"⚠️ Не удалось сгенерировать AI-видео: {str(e)[:200]}")
            return
        items = hf_items_from_clips(clips)
        if not items:
            await status.edit_text("⚠️ AI-видео не сгенерировалось. Попробуй повторить.")
            return
        try:
            await status.delete()
        except Exception:
            pass
        draft.source_items = items
        draft.status = Status.PREVIEW_READY
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
        await asyncio.to_thread(materialize_items, items, draft.work_dir)
        await _send_hf_preview(context, chat_id, draft, draft_id, with_clips=True, regen=False)
        return

    # Стале-callback на ещё-не-подключённый режим (Critical 3): не молчим.
    await context.bot.send_message(chat_id, "Этот режим скоро будет доступен.")


# ── Выбор голоса озвучки (ИИ-клон Максима ИЛИ свой голос) ─────────────────
_BROLL_OWNVOICE_STATE = "broll2_ownvoice"


# ── Инкремент 3: выбор фоновой музыки (до развилки голоса) ────────────────

_MUSIC_CAT_LABELS = {
    "chill": "😌 Chill", "energetic": "⚡ Энергичная", "cinematic": "🎬 Кинематограф",
    "corporate": "💼 Деловая", "inspiring": "✨ Вдохновляющая",
}


def _music_category_keyboard() -> InlineKeyboardMarkup:
    """Категории музыки (2 в ряд) + «Без музыки». Отмена реюзит broll_cancel."""
    rows, row = [], []
    for cat in list_categories().keys():
        row.append(InlineKeyboardButton(
            _MUSIC_CAT_LABELS.get(cat, cat), callback_data=f"b2mus:cat:{cat}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🚫 Без музыки", callback_data="b2mus:skip")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="broll_cancel")])
    return InlineKeyboardMarkup(rows)


def _music_picked_keyboard(category: str) -> InlineKeyboardMarkup:
    """После превью трека: принять / другой трек / сменить категорию / без музыки."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Эта музыка — дальше", callback_data="b2mus:accept")],
        [InlineKeyboardButton("🔄 Другой трек", callback_data=f"b2mus:reroll:{category}")],
        [InlineKeyboardButton("⬅️ Сменить категорию", callback_data="b2mus:back")],
        [InlineKeyboardButton("🚫 Без музыки", callback_data="b2mus:skip")],
    ])


async def start_broll_music_pick(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 chat_id: int | None = None) -> None:
    """«Собрать ролик» (broll_approve) → выбор музыки ДО развилки голоса. Выбор
    падает в broll_draft['music_path'] и подмешивается в монтаж на ОБОИХ
    голосовых форках (ИИ и свой) — одна точка вставки, полное покрытие."""
    draft = context.user_data.get("broll_draft")
    if not draft or not draft.get("script"):
        await context.bot.send_message(
            chat_id=chat_id or update.effective_chat.id,
            text="⚠️ Черновик потерян — собери ролик заново.")
        return
    if chat_id is None:
        chat_id = draft.get("chat_id") or update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text="🎵 <b>Фоновая музыка</b>\n\nВыбери настроение — добавлю тихим фоном "
             "под озвучку. Или «🚫 Без музыки».",
        parse_mode="HTML", reply_markup=_music_category_keyboard())


async def handle_broll_music_cb(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                action: str, category: str | None = None,
                                chat_id: int | None = None) -> None:
    """b2mus:* — cat/reroll (превью трека) · back (категории) · accept/skip
    (дальше к голосу). Только ЗАХВАТ пути; микширование делает ассемблер."""
    draft = context.user_data.get("broll_draft")
    if chat_id is None:
        chat_id = (draft or {}).get("chat_id") or update.effective_chat.id
    if not draft or not draft.get("script"):
        await context.bot.send_message(
            chat_id=chat_id, text="⚠️ Черновик потерян — собери ролик заново.")
        return

    if action == "back":
        draft.pop("music_path", None)
        await context.bot.send_message(
            chat_id=chat_id, text="🎵 Выбери категорию музыки:",
            reply_markup=_music_category_keyboard())
        return

    if action == "skip":
        draft.pop("music_path", None)
        await prompt_voice_choice(update, context)
        return

    if action == "accept":
        await prompt_voice_choice(update, context)
        return

    # cat / reroll — подобрать случайный трек категории и прислать превью.
    track = pick_random_track(category)
    if not track or not track.get("file"):
        draft.pop("music_path", None)
        await context.bot.send_message(
            chat_id=chat_id,
            text="🚫 В этой категории нет треков — выбери другую или «Без музыки».",
            reply_markup=_music_category_keyboard())
        return
    draft["music_path"] = track["file"]
    cat_label = _MUSIC_CAT_LABELS.get(category, category)
    with open(track["file"], "rb") as af:
        await context.bot.send_audio(
            chat_id=chat_id, audio=af,
            title=f"Музыка — {cat_label}",
            caption=f"🎧 {cat_label}. Подойдёт → «Эта музыка», иначе «Другой трек».",
            reply_markup=_music_picked_keyboard(category))


def _voice_choice_keyboard() -> InlineKeyboardMarkup:
    """Развилка озвучки: ИИ-клон Максима ИЛИ свой записанный голос."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Голос Максима (ИИ-клон)", callback_data="b2vc:ai")],
        [InlineKeyboardButton("🎤 Запишу сам (свой голос)", callback_data="b2vc:own")],
        [InlineKeyboardButton("❌ Отмена", callback_data="broll_cancel")],
    ])


def _voiceover_gate_keyboard() -> InlineKeyboardMarkup:
    """Гейт #2 (инкремент 2): превью ИИ-озвучки — принять и собрать, перегенерить,
    либо записать свой голос. Тяжёлый монтаж — только после «Собрать». «Записать
    свой» и «Отмена» реюзят существующие b2vc:own / broll_cancel."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Озвучка ок — собрать ролик", callback_data="b2vop:accept")],
        [InlineKeyboardButton("🔄 Перегенерировать", callback_data="b2vop:regen")],
        [InlineKeyboardButton("🎤 Записать свой голос", callback_data="b2vc:own")],
        [InlineKeyboardButton("❌ Отмена", callback_data="broll_cancel")],
    ])


def _ai_voice_path(uid: int) -> Path:
    """Стабильный путь превью-озвучки (1 файл/юзер, перезапись на regen) — как
    own-voice broll_ownvoice_{uid}.mp3. Переживает round-trip превью→accept
    в рамках живого процесса; DRAFTS_DIR читается в рантайме (тест его подменяет)."""
    d = DRAFTS_DIR.parent / "broll_voice"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"aivoice_{uid}.mp3"


async def prompt_voice_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«Собрать ролик» → спросить, каким голосом озвучивать (callback broll_approve)."""
    chat_id = update.callback_query.message.chat_id
    if not (context.user_data.get("broll_draft") or {}).get("script"):
        await context.bot.send_message(chat_id, "⚠️ Черновик потерян — собери ролик заново.")
        return
    await context.bot.send_message(
        chat_id,
        "🎙 <b>Каким голосом озвучить?</b>\n\n"
        "🤖 <b>ИИ-клон Максима</b> — мгновенно, но клон голоса пока не идеален.\n"
        "🎤 <b>Свой голос</b> — пришлёшь голосовое с прочитанным сценарием, "
        "соберу ролик на нём (субтитры лягут автоматом).",
        parse_mode="HTML", reply_markup=_voice_choice_keyboard())


async def start_broll_ownvoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«🎤 Запишу сам» → перевести в режим приёма голосового + показать сценарий."""
    chat_id = update.callback_query.message.chat_id
    uid = update.callback_query.from_user.id
    script = (context.user_data.get("broll_draft") or {}).get("script", "")
    if not script:
        await context.bot.send_message(chat_id, "⚠️ Черновик потерян — собери ролик заново.")
        return
    _bot_pending[uid] = {"state": _BROLL_OWNVOICE_STATE}
    _bot_save_pending(_bot_pending)
    await context.bot.send_message(
        chat_id,
        "🎤 Запиши голосовое — прочитай этот сценарий вслух:\n\n"
        f"<i>{html_mod.escape(script)}</i>\n\n"
        "Пришли голосовое — соберу ролик на твоём голосе.",
        parse_mode="HTML")


# ── Инкремент 2: превью ИИ-озвучки до монтажа ─────────────────────────────

async def preview_broll_voiceover(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    voiceover_fn,
    chat_id: int | None = None,
    status_fn=None,
) -> None:
    """b2vc:ai → СНАЧАЛА превью озвучки. Генерим ИИ-озвучку ОДИН раз в стабильный
    файл, шлём её аудио-превью + гейт. Тяжёлый монтаж — только после accept
    (тогда mp3 переиспользуется шимом, ElevenLabs второй раз не дёргается)."""
    draft = context.user_data.get("broll_draft")
    if not draft or not draft.get("script"):
        await context.bot.send_message(
            chat_id=chat_id or update.effective_chat.id,
            text="⚠️ Черновик потерян (бот мог рестартнуть). Собери ролик заново.",
        )
        return
    uid = _uid_from_update(update)
    if chat_id is None:
        chat_id = draft.get("chat_id") or update.effective_chat.id
    mp3 = _ai_voice_path(uid)
    status = await context.bot.send_message(
        chat_id=chat_id,
        text="🎙 Озвучиваю сценарий голосом Максима…\n<i>~10-30 сек</i>",
        parse_mode="HTML",
    )
    try:
        await asyncio.to_thread(voiceover_fn, draft["script"], str(mp3))
    except Exception as e:
        logger.error(f"[broll] voiceover preview failed: {e}", exc_info=True)
        try:
            await status.edit_text(f"❌ Озвучка не получилась: {e}")
        except Exception:
            pass
        return
    if not mp3.exists() or mp3.stat().st_size < 1000:
        await status.edit_text("❌ Озвучка вернула пустой файл. Нажми «🔄 Перегенерировать».")
        return
    draft["ai_voice_path"] = str(mp3)
    try:
        await status.delete()
    except Exception:
        pass
    with open(mp3, "rb") as af:
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=af,
            title="Озвучка (ИИ-голос Максима)",
            caption="🎧 Послушай озвучку. Ок → «Собрать ролик». Не нравится → "
                    "«Перегенерировать» (новая генерация) или «Записать свой голос».",
            reply_markup=_voiceover_gate_keyboard(),
        )


async def accept_broll_voiceover(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None = None,
    status_fn=None,
) -> None:
    """b2vop:accept → озвучка принята: собрать ролик, ПЕРЕИСПОЛЬЗУЯ уже
    сгенерённый mp3 через voiceover_fn-шим (copyfile) — как own-voice. assemble
    не модифицируется и ElevenLabs повторно не вызывается."""
    draft = context.user_data.get("broll_draft")
    if chat_id is None:
        chat_id = (draft or {}).get("chat_id") or update.effective_chat.id
    mp3 = (draft or {}).get("ai_voice_path")
    if not draft or not mp3 or not Path(mp3).exists() or Path(mp3).stat().st_size < 1000:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Озвучка не найдена — нажми «🔄 Перегенерировать».",
        )
        return

    def _reuse_voiceover(_script, out_path):
        shutil.copyfile(mp3, out_path)

    await assemble_broll_from_draft(
        update, context, _reuse_voiceover, chat_id=chat_id, status_fn=status_fn)


async def regen_broll_voiceover(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    voiceover_fn,
    chat_id: int | None = None,
    status_fn=None,
) -> None:
    """b2vop:regen → перегенерить ИИ-озвучку и снова показать превью (1 ElevenLabs
    на нажатие, by design — regen платный)."""
    await preview_broll_voiceover(
        update, context, voiceover_fn, chat_id=chat_id, status_fn=status_fn)


# ── Инкремент 4: гейт обложки (пост-сборка, кадр из ролика + текст) ────────

def _cover_picker_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """Пикер обложки: кадр (начало/середина/финал) + загрузка + библиотека +
    первый-кадр + отмена. Отмена реюзит broll_cancel."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📹 Начало", callback_data=f"b2cov:frame:{draft_id}:start"),
         InlineKeyboardButton("📹 Середина", callback_data=f"b2cov:frame:{draft_id}:mid"),
         InlineKeyboardButton("📹 Финал", callback_data=f"b2cov:frame:{draft_id}:end")],
        [InlineKeyboardButton("📤 Загрузить фото", callback_data=f"b2cov:upload:{draft_id}")],
        [InlineKeyboardButton("📚 Из библиотеки", callback_data=f"b2cov:library:{draft_id}")],
        [InlineKeyboardButton("➡️ Первый кадр", callback_data=f"b2cov:skip:{draft_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="broll_cancel")],
    ])


def _cover_lib_pick_keyboard(draft_id: str, photo_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ Выбрать эту", callback_data=f"b2cov:lib_pick:{draft_id}:{photo_id}")]])


def _cover_lib_footer_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё 6", callback_data=f"b2cov:lib_reroll:{draft_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"b2cov:back:{draft_id}")],
    ])


def _cover_confirm_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Эта обложка", callback_data=f"b2cov:confirm:{draft_id}")],
        [InlineKeyboardButton("🔄 Другой кадр", callback_data=f"b2cov:reject:{draft_id}")],
    ])


def _cover_text_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ С текстом", callback_data=f"b2cov:txt:{draft_id}:on")],
        [InlineKeyboardButton("➡️ Без текста", callback_data=f"b2cov:txt:{draft_id}:off")],
    ])


def _cover_validate(draft, draft_id: str) -> bool:
    """Защита от устаревшей кнопки (финал-экран живёт в чате долго): callback
    должен совпасть с текущим черновиком и иметь готовый монтаж."""
    return bool(draft) and draft.get("draft_id") == draft_id and bool(draft.get("final_path"))


async def start_broll_cover_pick(update, context, draft_id: str, chat_id=None) -> None:
    """b2cov:start — открыть пикер обложки (первый пост-сборочный гейт)."""
    draft = context.user_data.get("broll_draft")
    if chat_id is None:
        chat_id = (draft or {}).get("chat_id") or update.effective_chat.id
    if not _cover_validate(draft, draft_id):
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Это от прошлого ролика или черновик потерян — собери ролик заново.")
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text="🖼 <b>Обложка ролика</b>\n\nВыбери кадр — наложу обложку (с текстом или без). "
             "Или «➡️ Первый кадр».",
        parse_mode="HTML", reply_markup=_cover_picker_keyboard(draft_id))


async def handle_broll_cover_cb(update, context, action: str, draft_id: str, arg=None, *,
                                cover_fn=None, publish_fn=None, notion_cover_fn=None,
                                chat_id=None) -> None:
    """b2cov:* — кадр/skip (превью) · reject (пикер) · confirm (выбор текста) ·
    txt:on (ввод текста) · txt:off (готовая обложка без текста)."""
    draft = context.user_data.get("broll_draft")
    if chat_id is None:
        chat_id = (draft or {}).get("chat_id") or update.effective_chat.id
    if not _cover_validate(draft, draft_id):
        await context.bot.send_message(
            chat_id=chat_id, text="⚠️ Это от прошлого ролика — собери заново.")
        return

    if action in ("frame", "skip"):
        which = arg if action == "frame" else "start"
        final_path = draft["final_path"]
        dur = await asyncio.to_thread(probe_video_duration, final_path)
        ts_list = get_frame_timestamps(dur)
        ts = {"start": ts_list[0], "mid": ts_list[1], "end": ts_list[2]}.get(which, ts_list[0])
        out = Path(final_path).parent / f"cover_frame_{draft_id}.jpg"
        ok = await asyncio.to_thread(extract_frame, final_path, ts, str(out))
        if not ok:
            await context.bot.send_message(
                chat_id=chat_id, text="⚠️ Не удалось взять кадр. Выбери другой.",
                reply_markup=_cover_picker_keyboard(draft_id))
            return
        draft["cover_image"] = str(out)
        with open(out, "rb") as ph:
            await context.bot.send_photo(
                chat_id=chat_id, photo=ph,
                caption="Эта обложка? Текст добавим следующим шагом.",
                reply_markup=_cover_confirm_keyboard(draft_id))
        return

    if action == "reject":
        await context.bot.send_message(
            chat_id=chat_id, text="🖼 Выбери кадр:",
            reply_markup=_cover_picker_keyboard(draft_id))
        return

    if action == "confirm":
        if not draft.get("cover_image"):
            await context.bot.send_message(
                chat_id=chat_id, text="⚠️ Сначала выбери кадр.",
                reply_markup=_cover_picker_keyboard(draft_id))
            return
        await context.bot.send_message(
            chat_id=chat_id, text="Текст на обложке?",
            reply_markup=_cover_text_keyboard(draft_id))
        return

    if action == "upload":
        uid = _uid_from_update(update)
        _bot_pending[uid] = {"state": _COVER_UPLOAD_STATE, "cover_draft_id": draft_id}
        _bot_save_pending(_bot_pending)
        await context.bot.send_message(
            chat_id=chat_id,
            text="📤 Пришли фото для обложки одним сообщением (JPG/PNG).")
        return

    if action in ("library", "lib_reroll"):
        shown = draft.get("cover_lib_shown_ids", []) if action == "lib_reroll" else []
        sample = list_library_sample(6, exclude_ids=shown)
        if not sample:
            await context.bot.send_message(
                chat_id=chat_id, text="📚 Библиотека пуста — выбери кадр или загрузи фото.",
                reply_markup=_cover_picker_keyboard(draft_id))
            return
        draft["cover_lib_shown_ids"] = shown + [it["id"] for it in sample]
        for it in sample:
            try:
                with open(it["path"], "rb") as ph:
                    await context.bot.send_photo(
                        chat_id=chat_id, photo=ph, caption=f"<code>{it['id']}</code>",
                        parse_mode="HTML",
                        reply_markup=_cover_lib_pick_keyboard(draft_id, it["id"]))
            except Exception:
                continue
        await context.bot.send_message(
            chat_id=chat_id, text="Выбери фото «✅ Выбрать эту» или «🔄 Ещё 6».",
            reply_markup=_cover_lib_footer_keyboard(draft_id))
        return

    if action == "lib_pick":
        path = lookup_library_path(arg) if arg else None
        if not path or not Path(path).is_file():
            await context.bot.send_message(
                chat_id=chat_id, text="⚠️ Фото не найдено — выбери другое.",
                reply_markup=_cover_lib_footer_keyboard(draft_id))
            return
        draft["cover_image"] = path
        await context.bot.send_message(
            chat_id=chat_id, text="Текст на обложке?",
            reply_markup=_cover_text_keyboard(draft_id))
        return

    if action == "back":
        await context.bot.send_message(
            chat_id=chat_id, text="🖼 Выбери источник обложки:",
            reply_markup=_cover_picker_keyboard(draft_id))
        return

    if action == "txt":
        if arg == "on":
            uid = _uid_from_update(update)
            _bot_pending[uid] = {"state": _COVER_TEXT_STATE, "cover_draft_id": draft_id}
            _bot_save_pending(_bot_pending)
            await context.bot.send_message(
                chat_id=chat_id,
                text="✏️ Пришли текст для обложки одним сообщением (коротко — заголовок/хук).")
            return
        # «без текста» → готовая обложка-фото, финализация
        await _finalize_broll_cover(
            update, context, "", chat_id=chat_id,
            cover_fn=cover_fn, publish_fn=publish_fn, notion_cover_fn=notion_cover_fn)
        return


async def handle_broll_cover_text_message(update, context, *, cover_fn=None,
                                          publish_fn=None, notion_cover_fn=None) -> bool:
    """Приём текста обложки (state broll2_cover_text). Контракт -> bool."""
    uid = _uid_from_update(update)
    st = _bot_pending.get(uid)
    if not st or st.get("state") != _COVER_TEXT_STATE:
        return False
    text = (getattr(update.message, "text", "") or "").strip()
    if not text:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Пришли текст обложки сообщением.")
        return True
    _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
    await _finalize_broll_cover(
        update, context, text, chat_id=update.effective_chat.id,
        cover_fn=cover_fn, publish_fn=publish_fn, notion_cover_fn=notion_cover_fn)
    return True


async def handle_broll_cover_photo(update, context) -> bool:
    """Приём фото для обложки (state broll2_cover_upload). Контракт -> bool.
    Загрузка пропускает confirm (как selfie) → сразу выбор текста."""
    uid = _uid_from_update(update)
    st = _bot_pending.get(uid)
    if not st or st.get("state") != _COVER_UPLOAD_STATE:
        return False
    draft_id = st.get("cover_draft_id")
    draft = context.user_data.get("broll_draft")
    chat_id = (draft or {}).get("chat_id") or update.effective_chat.id
    msg = update.message
    photo = msg.photo[-1] if getattr(msg, "photo", None) else None
    doc = getattr(msg, "document", None)
    is_img_doc = bool(doc and (getattr(doc, "mime_type", "") or "").startswith("image/"))
    if not photo and not is_img_doc:
        await context.bot.send_message(
            chat_id=chat_id, text="Для обложки нужна картинка — пришли фото (JPG/PNG).")
        return True
    file_id = photo.file_id if photo else doc.file_id
    out = DRAFTS_DIR.parent / "broll_finals" / f"cover_upload_{uid}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(str(out))
    except Exception as e:
        logger.warning(f"[broll] cover upload download failed: {e}")
        await context.bot.send_message(
            chat_id=chat_id, text="⚠️ Не удалось принять фото — пришли ещё раз.")
        return True
    if not out.is_file() or out.stat().st_size < 100:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Пустое фото — пришли ещё раз.")
        return True
    _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
    if draft is not None:
        draft["cover_image"] = str(out)
    await context.bot.send_message(
        chat_id=chat_id, text="Текст на обложке?",
        reply_markup=_cover_text_keyboard(draft_id))
    return True


async def _finalize_broll_cover(update, context, cover_text: str, *, chat_id=None,
                                cover_fn=None, publish_fn=None, notion_cover_fn=None) -> None:
    """Рендер обложки на выбранном изображении → публикация + Notion (best-effort)
    → отдача пользователю → чистка персистнутого монтажа."""
    draft = context.user_data.get("broll_draft") or {}
    if chat_id is None:
        chat_id = draft.get("chat_id") or update.effective_chat.id
    image = draft.get("cover_image")
    # Гард: generate_cover без явного пути берёт СЛУЧАЙНЫЙ портрет Максима — нельзя.
    if not image or not Path(image).is_file():
        await context.bot.send_message(
            chat_id=chat_id, text="⚠️ Изображение обложки потеряно — выбери кадр заново.")
        return
    out = Path(image).parent / f"cover_final_{draft.get('draft_id', 'x')}.jpg"
    try:
        await asyncio.to_thread(cover_fn, cover_text, str(out), str(image))
    except Exception as e:
        logger.error(f"[broll] cover render failed: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Не удалось собрать обложку: {e}")
        return
    cover_url = None
    if publish_fn:
        try:
            cover_url = await asyncio.to_thread(publish_fn, str(out), "broll_cover")
        except Exception as e:
            logger.warning(f"[broll] cover publish failed: {e}")
    page_id = draft.get("notion_page_id")
    if cover_url and page_id and notion_cover_fn:
        try:
            await asyncio.to_thread(notion_cover_fn, page_id, cover_url)
        except Exception as e:
            logger.warning(f"[broll] notion cover patch failed (non-fatal): {e}")
    with open(out, "rb") as ph:
        await context.bot.send_photo(
            chat_id=chat_id, photo=ph,
            caption="✅ Обложка готова." + (f"\n🔗 {cover_url}" if cover_url else ""))
    # Монтаж больше не нужен — чистим персистнутую копию (не течёт диск).
    fp = draft.get("final_path")
    if fp:
        try:
            Path(fp).unlink(missing_ok=True)
        except Exception:
            pass
    draft["stage"] = "cover_done"


# ── #14: ручная пересборка одной HF-сцены ────────────────────────────────
async def _send_scene_clips(context, chat_id, source_items) -> None:
    """Шлёт N клипов сцен с подписями «Сцена N» (для превью и пикера пересборки)."""
    for i, it in enumerate(source_items, start=1):
        try:
            with open(it.path, "rb") as f:
                await context.bot.send_video(chat_id, f, caption=f"Сцена {i}")
        except Exception:
            await context.bot.send_message(chat_id, f"Сцена {i}: превью не отправилось")


def _hf_preview_text(n_scenes: int, regen: bool = True) -> str:
    """Короткое превью БЕЗ повтора сценария. regen=True (чистая графика) — подсказка
    про пересборку сцены; False (микс Авто+графика) — просто собрать."""
    head = (f"🎨 <b>Графика готова: {n_scenes} сцен(ы)</b> — клипы выше.\n\n"
            if regen else
            f"🎬 <b>Видеоряд готов: {n_scenes} клип(ов)</b> "
            f"(графика + живое видео) — выше.\n\n")
    regen_line = ("Кривая сцена → <b>«🔁 Перегенерировать сцену»</b> (выбери номер).\n"
                  if regen else "")
    return head + regen_line + (
        "Всё ок → <b>«✅ Собрать ролик»</b>: озвучка + монтаж + субтитры.")


async def _send_hf_preview(context, chat_id, draft, draft_id: str,
                           with_clips: bool = False, regen: bool = True) -> None:
    """Превью HF-ролика. with_clips → нумерованные клипы. regen=False (микс) —
    без кнопки пер-сценной пересборки (позиции в миксе ≠ scene_NN)."""
    clips = [str(it.path) for it in draft.source_items]
    context.user_data["broll_draft"] = {
        "script": draft.script_text, "clips": clips, "theme": draft.theme,
        "notion_url": draft.notion_url, "notion_page_id": draft.notion_page_id,
        "chat_id": draft.chat_id,
        # AI-видео (Seedance) → нарративная сборка: клипы целиком, по порядку, без кругов.
        "narrative": draft.source_mode == SourceMode.AI_VIDEO,
    }
    if with_clips:
        await _send_scene_clips(context, chat_id, draft.source_items)
    await context.bot.send_message(
        chat_id, _hf_preview_text(len(clips), regen=regen), parse_mode="HTML",
        reply_markup=_approval_keyboard(
            draft.notion_url, hf_draft_id=draft_id if regen else None),
        disable_web_page_preview=True)


async def _generate_hf_clips(context, chat_id, draft, draft_id: str, source_mode: str,
                             allow_live_fallback: bool = False):
    """Генерация HF-графики — ОБЩАЯ для HF_ONLY и AUTO_HF: work_dir + прогресс-мост
    thread→Telegram + обработка Interrupted/Timeout/Exception. Возвращает list[Path]
    клипов или None при сбое.

    allow_live_fallback=False (HF_ONLY): при сбое показывает retry-UI (юзер выбрал
    именно графику — не подменять молча). True (AUTO_HF): графика опциональна —
    при сбое тихо убирает статус и возвращает None, решение отдаёт caller'у."""
    from hyperframes_broll import (
        generate_hyperframes_broll, HyperFramesInterrupted, HyperFramesTimeout,
    )
    work = DRAFTS_DIR.parent / "broll_runs" / draft_id
    work.mkdir(parents=True, exist_ok=True)
    draft.source_mode = source_mode
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
    loop = asyncio.get_running_loop()
    _msg_id = status.message_id

    def _hf_progress(text: str) -> None:
        fut = asyncio.run_coroutine_threadsafe(
            context.bot.edit_message_text(
                chat_id=chat_id, message_id=_msg_id, text=f"{header}\n\n{text}"),
            loop)
        fut.add_done_callback(lambda f: f.exception())

    def _retry_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Повторить графику",
                                  callback_data=f"b2src:{source_mode}:{draft_id}")],
            [InlineKeyboardButton("❌ Отмена", callback_data=f"b2src:cancel:{draft_id}")],
        ])

    async def _fail(msg: str):
        # AUTO_HF: графика опциональна → тихо снимаем статус, caller решит (live_only).
        # HF_ONLY: юзер выбрал графику → retry-UI, не подменяем молча.
        if allow_live_fallback:
            try:
                await status.delete()
            except Exception:
                pass
        else:
            await status.edit_text(msg, reply_markup=_retry_kb())
        return None

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
        return await _fail(
            "🔁 Графика не успела собраться — сервис перезапускался во время "
            "рендера. Сценарий на месте, жми «Повторить».")
    except HyperFramesTimeout as e:
        return await _fail(
            f"⏱ {str(e)[:200]}\n\nГрафика не уложилась в лимит. Можно повторить "
            f"или сократить сценарий.")
    except Exception as e:
        logger.error(f"[broll] HF generation failed: {e}", exc_info=True)
        return await _fail(f"⚠️ Не удалось сгенерировать графику: {str(e)[:200]}")
    if not clips:
        return await _fail("⚠️ Графика не сгенерировалась (0 сцен). Попробуй повторить.")
    try:
        await status.delete()
    except Exception:
        pass
    return clips


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
    await _send_scene_clips(context, chat_id, draft.source_items)
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
            "notion_page_id": draft.notion_page_id,
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
        "notion_page_id": draft.notion_page_id,
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
    status_fn=None,
) -> None:
    """Фаза 2: озвучка → ffmpeg-монтаж → субтитры → отправка MP4.

    voiceover_fn — функция generate_voiceover(text, out_path) из bot.py
    (передаётся параметром, чтобы не плодить циклический импорт).
    status_fn — update_notion_status(page_id, status) из bot.py: при готовом
    ролике двигаем карточку на Kanban в «Готово к публикации».
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
    notion_page_id = draft.get("notion_page_id")
    music_path = draft.get("music_path")  # инкремент 3: фон под озвучку (None = без музыки)
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
                music_path, draft.get("narrative", False),
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

        # Инкремент 4: монтаж рождается тут и удаляется в finally. Сохраняем
        # per-user АТОМАРНО, чтобы пост-сборочный гейт обложки взял из него кадр.
        cover_draft_id = context.user_data.get("broll_draft_id")
        cover_final = None
        if cover_draft_id:
            try:
                _dst = _broll_final_path(_uid_from_update(update))
                _tmp = _dst.with_suffix(".mp4.part")
                await asyncio.to_thread(shutil.copy2, str(final_path), str(_tmp))
                _tmp.replace(_dst)
                if _dst.exists() and _dst.stat().st_size > 1000:
                    cover_final = str(_dst)
            except Exception as e:
                logger.warning(f"[broll] persist final for cover failed: {e}")

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

        # Карточка на Kanban → «Готово к публикации» (ролик собран = идея реализована).
        if notion_page_id and status_fn:
            try:
                await asyncio.to_thread(status_fn, notion_page_id, "Готово к публикации")
            except Exception as e:
                logger.warning(f"[broll] статус карточки не обновлён: {e}")

        action_rows = []
        # Инкремент 4: обложка — первый пост-сборочный шаг (если монтаж сохранён).
        if cover_final and cover_draft_id:
            draft["final_path"] = cover_final
            draft["draft_id"] = cover_draft_id
            draft["stage"] = "assembled"
            action_rows.append([InlineKeyboardButton(
                "🖼 Сделать обложку", callback_data=f"b2cov:start:{cover_draft_id}")])
        action_rows.append([InlineKeyboardButton("🔄 Ещё B-roll ролик", callback_data="broll_regen")])
        if notion_url:
            action_rows.append([InlineKeyboardButton("📋 К карточке", url=notion_url)])
        action_rows.append([InlineKeyboardButton("◀️ В главное меню", callback_data="idea_back_to_menu")])
        await context.bot.send_message(
            chat_id=chat_id,
            text="Готово. Что дальше?",
            reply_markup=InlineKeyboardMarkup(action_rows),
        )

        # Черновик НЕ схлопываем, если доступна обложка (гейту нужны final_path/draft_id).
        if not (cover_final and cover_draft_id):
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


# ── Инкремент 1: гейт правки/утверждения сценария (до меню источника) ──

def _uid_from_update(update) -> int:
    if getattr(update, "effective_user", None):
        return update.effective_user.id
    q = getattr(update, "callback_query", None)
    return q.from_user.id if q else 0


async def _send_script_gate(context, draft) -> None:
    """Показать сценарий + клавиатуру гейта (Править / Утвердить)."""
    await context.bot.send_message(
        chat_id=draft.chat_id,
        text=(
            f"🎞 <b>B-roll ролик</b> — сценарий\n\n"
            f"<i>{html_mod.escape(draft.script_text)}</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>«✏️ Править»</b> — поправить текст, "
            f"<b>«✅ Утвердить»</b> — перейти к выбору видеоряда."
        ),
        parse_mode="HTML",
        reply_markup=_script_gate_keyboard(draft.draft_id),
        disable_web_page_preview=True,
    )


async def start_broll_script_edit(update, context, draft_id: str) -> None:
    """b2scr:edit — войти в режим правки сценария свободным текстом.

    Реюз 2-state паттерна селфи (callback ставит состояние → следующий текст
    применяется), но БЕЗ apply_user_edits: B-roll сценарий не привязан к
    таймкодам субтитров, правится как угодно по объёму."""
    draft = load_draft(draft_id, DRAFTS_DIR)
    if draft is None:
        await context.bot.send_message(
            chat_id=(update.effective_chat.id if getattr(update, "effective_chat", None) else 0),
            text="⚠️ Черновик устарел — запусти B-roll ролик заново.",
        )
        return
    uid = _uid_from_update(update)
    _bot_pending[uid] = {"state": _EDIT_SCRIPT_STATE, "broll_edit_draft_id": draft_id}
    _bot_save_pending(_bot_pending)
    await context.bot.send_message(
        chat_id=draft.chat_id,
        text=(
            f"✏️ <b>Правка сценария</b>\n\n"
            f"Текущий текст:\n<i>{html_mod.escape(draft.script_text)}</i>\n\n"
            f"Пришли исправленный сценарий одним сообщением — заменю целиком. "
            f"Объём любой."
        ),
        parse_mode="HTML",
        reply_markup=_script_editing_keyboard(draft_id),
        disable_web_page_preview=True,
    )


async def handle_script_edit_message(update, context) -> bool:
    """Приём исправленного сценария (state broll2_edit_script).

    Контракт-зеркало handle_broll2_upload_message (-> bool: True, если
    сообщение обработано этим хендлером). БЕЗ валидации количества слов —
    любой текст заменяет draft.script_text целиком."""
    uid = _uid_from_update(update)
    st = _bot_pending.get(uid)
    if not st or st.get("state") != _EDIT_SCRIPT_STATE:
        return False
    draft_id = st.get("broll_edit_draft_id")
    new_text = (getattr(update.message, "text", "") or "").strip()
    if not new_text:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Пришли текст сценария сообщением (или «⬅️ Отмена правки»).",
            reply_markup=_script_editing_keyboard(draft_id),
        )
        return True
    draft = load_draft(draft_id, DRAFTS_DIR)
    if draft is None:
        _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Черновик устарел — запусти B-roll ролик заново.",
        )
        return True
    # Перезаписываем, только если текст реально изменился (по словам). Без
    # предупреждений — B-roll сценарий свободный, любой объём допустим.
    if new_text.split() != draft.script_text.split():
        draft.script_text = new_text
        draft.touch(time.time())
        save_draft(draft, DRAFTS_DIR)
    _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
    await _send_script_gate(context, draft)
    return True


async def approve_broll_script(update, context, draft_id: str) -> None:
    """b2scr:ok — сценарий утверждён, показать меню источника видеоряда.
    Перенесённый из generate_broll_preview вызов source_menu_keyboard;
    handle_broll_source (b2src) не трогаем."""
    draft = load_draft(draft_id, DRAFTS_DIR)
    if draft is None:
        await context.bot.send_message(
            chat_id=(update.effective_chat.id if getattr(update, "effective_chat", None) else 0),
            text="⚠️ Черновик устарел — запусти B-roll ролик заново.",
        )
        return
    await context.bot.send_message(
        chat_id=draft.chat_id,
        text="✅ Сценарий утверждён.\n\n━━━━━━━━━━━━━━━━━━━━━\nОткуда взять видеоряд?",
        parse_mode="HTML",
        reply_markup=source_menu_keyboard(draft.draft_id, enabled_modes=_ENABLED_MODES),
    )


async def cancel_broll_script_edit(update, context, draft_id: str) -> None:
    """b2scr:cancel_edit — выйти из правки обратно на гейт без изменений."""
    uid = _uid_from_update(update)
    if _bot_pending.get(uid, {}).get("state") == _EDIT_SCRIPT_STATE:
        _bot_pending.pop(uid, None); _bot_save_pending(_bot_pending)
    draft = load_draft(draft_id, DRAFTS_DIR)
    if draft is None:
        await context.bot.send_message(
            chat_id=(update.effective_chat.id if getattr(update, "effective_chat", None) else 0),
            text="⚠️ Черновик устарел — запусти B-roll ролик заново.",
        )
        return
    await _send_script_gate(context, draft)


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
    "start_broll_script_edit",
    "handle_script_edit_message",
    "approve_broll_script",
    "cancel_broll_script_edit",
    "preview_broll_voiceover",
    "accept_broll_voiceover",
    "regen_broll_voiceover",
    "start_broll_music_pick",
    "handle_broll_music_cb",
    "start_broll_cover_pick",
    "handle_broll_cover_cb",
    "handle_broll_cover_text_message",
    "handle_broll_cover_photo",
]
