"""
Telegram handlers for on-demand fal.ai generation.

Two user-facing entry points:
    /image  — one photo via Nano Banana Pro
    /video  — one 5s or 10s clip via Kling 3.0 Pro (text-to-video)

Prompts can be typed OR dictated via voice message (Groq Whisper transcription
happens in the caller via process_voice; we receive plain text both ways).

Pricing is logged internally only — never shown in the UI:
    image    — $0.15
    video 5s — $0.56 (audio-off)
    video 10s — $1.12 (audio-off)

Register via:
    from fal_handlers import register_fal_handlers
    register_fal_handlers(app, pending, save_pending_fn)

State machine (stored in pending[user_id]["state"]):
    fal_image_prompt      — waiting for image prompt (text or voice)
    fal_video_prompt_5    — waiting for 5s video prompt
    fal_video_prompt_10   — waiting for 10s video prompt
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import fal_media

logger = logging.getLogger(__name__)

# Internal pricing map (for logs + future billing). NEVER shown to users.
_PRICES_USD = {
    "image": 0.15,
    "video_5": 0.56,
    "video_10": 1.12,
}


# Injected at registration time
_pending: dict = {}
_save_pending: Callable | None = None


# --- State predicates -----------------------------------------------------

_IMAGE_STATE = "fal_image_prompt"
_VIDEO_STATES = {"fal_video_prompt_5", "fal_video_prompt_10"}
_ALL_STATES = {_IMAGE_STATE, *_VIDEO_STATES}


def is_fal_state(state: str | None) -> bool:
    """Return True if the given pending-state is owned by fal_handlers."""
    return state in _ALL_STATES


async def consume_fal_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> bool:
    """Called by process_idea / process_voice when the active state is one of ours.

    Returns True if the prompt was consumed (caller should stop further processing).
    """
    user_id = update.effective_user.id
    state = _pending.get(user_id, {}).get("state")
    if state not in _ALL_STATES:
        return False

    prompt = (prompt or "").strip()
    if not prompt:
        await update.message.reply_text("Пустой промпт — попробуй ещё раз.")
        return True

    # Clear state immediately so double-sends don't retrigger
    _pending[user_id]["state"] = None
    if _save_pending:
        _save_pending(_pending)

    if state == _IMAGE_STATE:
        await _run_image(update, context, prompt)
    elif state == "fal_video_prompt_5":
        await _run_video(update, context, prompt, duration=5)
    elif state == "fal_video_prompt_10":
        await _run_video(update, context, prompt, duration=10)
    return True


# --- Commands -------------------------------------------------------------

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/image — one-shot photo generator."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] /image")
    _pending.setdefault(user_id, {})["state"] = _IMAGE_STATE
    if _save_pending:
        _save_pending(_pending)
    await update.message.reply_text(
        "🖼 Сгенерирую одно фото.\n\n"
        "Опиши, что нарисовать — текстом или голосом.\n"
        "Например: «минималистичный логотип бренда кофе на чёрном фоне, оранжевые акценты»."
    )


async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/video — pick duration first, then wait for prompt."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] /video")
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏱ 5 секунд", callback_data="fal:dur:5"),
            InlineKeyboardButton("⏱ 10 секунд", callback_data="fal:dur:10"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="fal:cancel")],
    ])
    await update.message.reply_text(
        "🎬 Сгенерирую одно видео (Kling 3.0 Pro, 1080×1920, 9:16).\n\n"
        "Выбери длительность:",
        reply_markup=kb,
    )


async def _on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for fal:cancel — clear state and acknowledge."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = _pending.get(user_id, {}).get("state")
    if state in _ALL_STATES or state is None:
        if user_id in _pending:
            _pending[user_id]["state"] = None
            if _save_pending:
                _save_pending(_pending)
    await query.edit_message_text("❌ Отменено.")


async def _on_duration_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for fal:dur:5 / fal:dur:10."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data or ""
    try:
        duration = int(data.rsplit(":", 1)[-1])
    except ValueError:
        await query.edit_message_text("Ошибка: неизвестная длительность.")
        return
    if duration not in (5, 10):
        await query.edit_message_text("Ошибка: длительность должна быть 5 или 10.")
        return
    _pending.setdefault(user_id, {})["state"] = f"fal_video_prompt_{duration}"
    if _save_pending:
        _save_pending(_pending)
    await query.edit_message_text(
        f"⏱ Длительность: {duration} сек.\n\n"
        f"Опиши видео — текстом или голосом.\n"
        f"Например: «замедленная съёмка, капля кофе падает в чашку, тёплый свет».",
    )


# --- Generation runners ---------------------------------------------------

async def _run_image(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    user_id = update.effective_user.id
    msg = await update.message.reply_text("⏳ Рисую фото... (около 30 сек)")
    logger.info(
        f"[user:{user_id}] fal image generation starting: "
        f"cost_usd={_PRICES_USD['image']} prompt={prompt[:100]!r}"
    )
    try:
        path = await asyncio.to_thread(fal_media.generate_image, prompt, "9:16")
    except Exception as e:
        logger.error(f"[user:{user_id}] image generation crashed: {e}", exc_info=True)
        await msg.edit_text(f"❌ Ошибка генерации: {e}")
        return

    if not path:
        await msg.edit_text(
            "❌ Не получилось сгенерировать фото. "
            "Проверь логи — возможно, закончился баланс или упал API.",
        )
        return

    try:
        with open(path, "rb") as f:
            await update.message.reply_photo(photo=f, caption=f"✅ Готово\n\n«{prompt[:200]}»")
        await msg.delete()
    except Exception as e:
        logger.error(f"[user:{user_id}] send photo failed: {e}", exc_info=True)
        await msg.edit_text(f"Файл сохранён: {path}\n\nНе смог отправить в Telegram: {e}")


async def _run_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    duration: int,
):
    user_id = update.effective_user.id
    msg = await update.message.reply_text(
        f"🎬 Генерирую видео ({duration} сек)... Это может занять 1–3 минуты."
    )
    price_key = f"video_{duration}"
    logger.info(
        f"[user:{user_id}] fal video generation starting: "
        f"duration={duration}s cost_usd={_PRICES_USD[price_key]} "
        f"prompt={prompt[:100]!r}"
    )
    try:
        path = await asyncio.to_thread(fal_media.generate_video, prompt, duration, "9:16")
    except Exception as e:
        logger.error(f"[user:{user_id}] video generation crashed: {e}", exc_info=True)
        await msg.edit_text(f"❌ Ошибка генерации: {e}")
        return

    if not path:
        await msg.edit_text(
            "❌ Не получилось сгенерировать видео. "
            "Проверь логи — возможно, закончился баланс или упал API.",
        )
        return

    try:
        with open(path, "rb") as f:
            await update.message.reply_video(
                video=f,
                caption=f"✅ Готово ({duration} сек)\n\n«{prompt[:200]}»",
                supports_streaming=True,
            )
        await msg.delete()
    except Exception as e:
        logger.error(f"[user:{user_id}] send video failed: {e}", exc_info=True)
        await msg.edit_text(f"Файл сохранён: {path}\n\nНе смог отправить в Telegram: {e}")


# --- Registration ---------------------------------------------------------

def register_fal_handlers(app: Application, pending: dict, save_pending_fn: Callable):
    """Wire /image, /video commands and their callback/state hooks into the bot."""
    global _pending, _save_pending
    _pending = pending
    _save_pending = save_pending_fn

    app.add_handler(CommandHandler("image", image_command))
    app.add_handler(CommandHandler("video", video_command))
    # Callback for duration picker — registered with specific pattern so it
    # resolves BEFORE the generic handle_callback catch-all.
    app.add_handler(CallbackQueryHandler(_on_duration_pick, pattern=r"^fal:dur:\d+$"))
    app.add_handler(CallbackQueryHandler(_on_cancel, pattern=r"^fal:cancel$"))
    logger.info("fal_handlers registered: /image, /video, fal:dur:*, fal:cancel callbacks")
