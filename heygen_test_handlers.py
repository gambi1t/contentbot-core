"""
Telegram handlers for HeyGen Image-to-Video test command.

User-facing entry point:
    /heygen_test — прогоняет одно фото + одно аудио через HeyGen Image-to-Video
                    и присылает готовое mp4 в чат.

Flow (state machine via pending[user_id]["state"]):
    heygen_test_photo  → ждём фото (PNG/JPG)
    heygen_test_audio  → ждём аудио (mp3 / голосовое)
    heygen_test_pick   → ждём выбор Avatar 3 / Avatar 4 (callback)
    (далее запуск + поллинг — без отдельного state)

UI деликатно нейтральный — без упоминания клиентов / бренда / стоимости.
Внутри логов — duration, версия, цена в долларах для нашего анализа.

API path: HeyGen v3 Image-to-Video.
    POST /v3/videos      type:"image" + image.url + audio_url + version flag
    GET  /v3/videos/{id} polling

Pricing (для логов, не для UI):
    Avatar III  $0.0167 / sec  (1080p photo avatar)
    Avatar IV   $0.05   / sec  (1080p photo avatar)

Register via:
    from heygen_test_handlers import register_heygen_test_handlers
    register_heygen_test_handlers(app, pending, save_pending_fn,
                                   save_media_fn, save_image_fn,
                                   heygen_api_key)
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from typing import Callable

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)

# Pricing for internal logs only — НЕ показывать в UI
_PRICE_USD_PER_SEC = {
    "v3": 0.0167,  # Avatar III @ 1080p photo avatar
    "v4": 0.05,    # Avatar IV @ 1080p photo avatar
}

# Injected at registration time
_pending: dict = {}
_save_pending: Callable | None = None
_save_media: Callable | None = None
_save_image: Callable | None = None
_heygen_api_key: str = ""

# State constants
STATE_PHOTO = "heygen_test_photo"
STATE_AUDIO = "heygen_test_audio"
STATE_PICK = "heygen_test_pick"
_ALL_STATES = {STATE_PHOTO, STATE_AUDIO, STATE_PICK}


def is_heygen_test_state(state: str | None) -> bool:
    """Return True if pending state belongs to /heygen_test flow."""
    return state in _ALL_STATES


# --- Command entrypoint ---------------------------------------------------

async def heygen_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/heygen_test — start photo+audio test flow."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] /heygen_test")
    _pending.setdefault(user_id, {})["state"] = STATE_PHOTO
    _pending[user_id].pop("heygen_test_photo_url", None)
    _pending[user_id].pop("heygen_test_audio_url", None)
    if _save_pending:
        _save_pending(_pending)
    await update.message.reply_text(
        "🎬 Тест аватара.\n\n"
        "Отправь фото человека (PNG или JPG, желательно анфас, по грудь, без очков и предметов в руках)."
    )


# --- Step 1: photo --------------------------------------------------------

async def consume_heygen_test_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Called by photo handler when state == STATE_PHOTO. Returns True if consumed."""
    user_id = update.effective_user.id
    state = _pending.get(user_id, {}).get("state")
    if state != STATE_PHOTO:
        return False

    photo = update.message.photo[-1] if update.message.photo else None
    document = update.message.document
    if not photo and not document:
        await update.message.reply_text("Не вижу фото. Пришли картинку как фото или файл.")
        return True

    msg = await update.message.reply_text("📸 Принял фото. Сохраняю...")

    try:
        if photo:
            tg_file = await context.bot.get_file(photo.file_id)
            suffix = ".jpg"
        else:
            tg_file = await context.bot.get_file(document.file_id)
            suffix = os.path.splitext(document.file_name or "image.jpg")[1] or ".jpg"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        # Public URL via nginx /media/ (image goes through same media hosting
        # as audio — both end up under /srv/bot-media/ via symlink).
        public_url = await asyncio.to_thread(_save_image, tmp_path, "heygen_test_img")
        _pending[user_id]["heygen_test_photo_url"] = public_url
        _pending[user_id]["state"] = STATE_AUDIO
        if _save_pending:
            _save_pending(_pending)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        logger.info(f"[user:{user_id}] heygen_test photo saved: {public_url}")
        await msg.edit_text(
            "✅ Фото сохранено.\n\n"
            "Теперь отправь аудио — голосовым сообщением или mp3-файлом."
        )
    except Exception as e:
        logger.error(f"[user:{user_id}] heygen_test photo save failed: {e}", exc_info=True)
        await msg.edit_text(f"❌ Не получилось сохранить фото: {e}")
        _pending[user_id]["state"] = None
        if _save_pending:
            _save_pending(_pending)
    return True


# --- Step 2: audio --------------------------------------------------------

async def consume_heygen_test_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Called by voice/audio handler when state == STATE_AUDIO. Returns True if consumed."""
    user_id = update.effective_user.id
    state = _pending.get(user_id, {}).get("state")
    if state != STATE_AUDIO:
        return False

    voice = update.message.voice or update.message.audio
    document = update.message.document
    if not voice and not (document and document.mime_type and "audio" in document.mime_type):
        await update.message.reply_text("Не вижу аудио. Пришли голосовое или mp3-файл.")
        return True

    msg = await update.message.reply_text("🎤 Принял аудио. Сохраняю...")

    try:
        if voice:
            tg_file = await context.bot.get_file(voice.file_id)
            suffix = ".ogg" if update.message.voice else ".mp3"
        else:
            tg_file = await context.bot.get_file(document.file_id)
            suffix = os.path.splitext(document.file_name or "audio.mp3")[1] or ".mp3"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        public_url = await asyncio.to_thread(_save_media, tmp_path, "heygen_test_audio")
        _pending[user_id]["heygen_test_audio_url"] = public_url
        _pending[user_id]["state"] = STATE_PICK
        if _save_pending:
            _save_pending(_pending)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        logger.info(f"[user:{user_id}] heygen_test audio saved: {public_url}")

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Avatar 3", callback_data="heygen_test:v3"),
                InlineKeyboardButton("Avatar 4", callback_data="heygen_test:v4"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="heygen_test:cancel")],
        ])
        await msg.edit_text(
            "✅ Аудио сохранено.\n\nВыбери версию аватара:",
            reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"[user:{user_id}] heygen_test audio save failed: {e}", exc_info=True)
        await msg.edit_text(f"❌ Не получилось сохранить аудио: {e}")
        _pending[user_id]["state"] = None
        if _save_pending:
            _save_pending(_pending)
    return True


# --- Step 3: avatar version pick ------------------------------------------

async def _on_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for heygen_test:v3 / heygen_test:v4 / heygen_test:cancel."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data or ""
    state = _pending.get(user_id, {}).get("state")
    if state != STATE_PICK:
        try:
            await query.edit_message_text("Сессия теста устарела. Запусти /heygen_test заново.")
        except Exception:
            pass
        return

    if data == "heygen_test:cancel":
        _pending[user_id]["state"] = None
        if _save_pending:
            _save_pending(_pending)
        await query.edit_message_text("❌ Отменено.")
        return

    avatar_version = data.split(":", 1)[1]  # "v3" or "v4"
    if avatar_version not in ("v3", "v4"):
        await query.edit_message_text("Неизвестная версия. Запусти /heygen_test заново.")
        return

    photo_url = _pending[user_id].get("heygen_test_photo_url")
    audio_url = _pending[user_id].get("heygen_test_audio_url")
    if not photo_url or not audio_url:
        await query.edit_message_text(
            "Что-то пропало из контекста — нет фото или аудио. Запусти /heygen_test заново."
        )
        _pending[user_id]["state"] = None
        if _save_pending:
            _save_pending(_pending)
        return

    # Clear state immediately so re-clicks don't retrigger
    _pending[user_id]["state"] = None
    if _save_pending:
        _save_pending(_pending)

    label = "Avatar 4" if avatar_version == "v4" else "Avatar 3"
    await query.edit_message_text(f"⏳ Генерирую через {label}... Это займёт 1–3 минуты.")

    # Kick off the actual HeyGen request + polling
    asyncio.create_task(
        _run_heygen_test(update, context, user_id, photo_url, audio_url, avatar_version, query)
    )


# --- Background runner ----------------------------------------------------

async def _run_heygen_test(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    photo_url: str,
    audio_url: str,
    avatar_version: str,
    progress_query,
):
    """Submit to HeyGen v3, poll, send result back to user."""
    label = "Avatar 4" if avatar_version == "v4" else "Avatar 3"
    started_at = time.time()

    try:
        video_id = await asyncio.to_thread(
            _heygen_v3_image_to_video, photo_url, audio_url, avatar_version
        )
        logger.info(
            f"[user:{user_id}] heygen_test submitted: video_id={video_id} "
            f"version={avatar_version} photo={photo_url[:60]}... audio={audio_url[:60]}..."
        )
    except Exception as e:
        logger.error(f"[user:{user_id}] heygen_test submit failed: {e}", exc_info=True)
        try:
            await progress_query.edit_message_text(f"❌ Ошибка отправки в HeyGen: {e}")
        except Exception:
            pass
        return

    # Poll up to 10 minutes
    video_url = None
    duration_sec = None
    for _ in range(120):  # 120 * 5 sec = 10 min
        await asyncio.sleep(5)
        try:
            status_data = await asyncio.to_thread(_heygen_v3_check_status, video_id)
        except Exception as e:
            logger.warning(f"[user:{user_id}] heygen_test poll error: {e}")
            continue

        status = status_data.get("status")
        if status == "completed":
            video_url = status_data.get("video_url")
            duration_sec = status_data.get("duration")
            break
        if status == "failed":
            err = status_data.get("failure_message") or status_data.get("error") or "unknown"
            logger.error(f"[user:{user_id}] heygen_test failed: {err}")
            try:
                await progress_query.edit_message_text(f"❌ HeyGen вернул ошибку: {err}")
            except Exception:
                pass
            return

    if not video_url:
        logger.error(f"[user:{user_id}] heygen_test timeout: video_id={video_id}")
        try:
            await progress_query.edit_message_text(
                "⌛ Превышено время ожидания (10 мин). Видео может быть готово позже — "
                f"id={video_id}, проверь в HeyGen Dashboard."
            )
        except Exception:
            pass
        return

    # Internal cost log (NOT shown in UI)
    elapsed = time.time() - started_at
    if duration_sec:
        cost_usd = duration_sec * _PRICE_USD_PER_SEC[avatar_version]
        logger.info(
            f"[user:{user_id}] heygen_test done: video_id={video_id} "
            f"version={avatar_version} duration={duration_sec:.1f}s "
            f"cost_usd={cost_usd:.3f} elapsed_total={elapsed:.0f}s url={video_url}"
        )
    else:
        logger.info(
            f"[user:{user_id}] heygen_test done (no duration in response): "
            f"video_id={video_id} version={avatar_version} elapsed={elapsed:.0f}s"
        )

    # Download → permanent copy → compress if needed → send with fallbacks.
    # HeyGen URL is a signed AWS S3 link with Expires=... — protухнет за пару
    # часов. Сохраняем permanent копию на нашем nginx чтобы клиент мог
    # пересмотреть видео завтра/послезавтра.
    await _deliver_heygen_video(context, user_id, video_url, label, progress_query)


async def _deliver_heygen_video(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    video_url: str,
    label: str,
    progress_query,
):
    """Download HeyGen mp4, save permanent copy, compress if needed, send to
    Telegram with progressive fallbacks.

    Fallback ladder:
      1. send_video (in-chat player, preview) — primary
      2. send_document (если send_video не пролез — на мобильном дает явную
         кнопку «Скачать», а Telegram иногда мягче с лимитами на document)
      3. plain text-сообщение с permanent URL — последняя соломинка
    """
    import os
    import shutil
    import subprocess
    import tempfile

    TG_LIMIT_BYTES = 49 * 1024 * 1024  # 49 MB safety margin under Telegram's 50 MB
    tmp_dir = tempfile.mkdtemp(prefix="heygen_test_")
    raw_path = os.path.join(tmp_dir, "raw.mp4")
    permanent_url: str | None = None

    try:
        # 1. Download HeyGen file (URL expires in hours, copy locally now)
        try:
            def _download_sync(url: str, dest: str):
                with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in r.iter_bytes(64 * 1024):
                            f.write(chunk)

            await asyncio.to_thread(_download_sync, video_url, raw_path)
        except Exception as e:
            logger.error(f"[user:{user_id}] heygen download failed: {e}", exc_info=True)
            try:
                await progress_query.edit_message_text(
                    f"⚠️ Готово ({label}), но не смог скачать на сервер.\n"
                    f"Прямая ссылка (быстро протухнет):\n{video_url}"
                )
            except Exception:
                pass
            return

        raw_size = os.path.getsize(raw_path)
        logger.info(
            f"[user:{user_id}] heygen video downloaded: {raw_size / 1024 / 1024:.1f} MB"
        )

        # 2. Save permanent copy on our server (nginx /media/, не протухает)
        try:
            permanent_url = await asyncio.to_thread(_save_media, raw_path, "heygen_test_video")
            logger.info(f"[user:{user_id}] heygen permanent saved: {permanent_url}")
        except Exception as e:
            logger.error(f"[user:{user_id}] permanent save failed: {e}", exc_info=True)

        # 3. Compress if larger than Telegram limit
        send_path = raw_path
        if raw_size > TG_LIMIT_BYTES:
            compressed_path = os.path.join(tmp_dir, "compressed.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", raw_path,
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "copy",
                "-movflags", "+faststart",
                compressed_path,
            ]
            try:
                await asyncio.to_thread(
                    subprocess.run, cmd, check=True, capture_output=True, timeout=180
                )
                new_size = os.path.getsize(compressed_path)
                logger.info(
                    f"[user:{user_id}] heygen compressed: "
                    f"{raw_size / 1024 / 1024:.1f} MB → {new_size / 1024 / 1024:.1f} MB"
                )
                send_path = compressed_path
            except Exception as e:
                logger.error(f"[user:{user_id}] ffmpeg compress failed: {e}", exc_info=True)
                # стараемся слать оригинал — Telegram сам решит

        caption = f"✅ Готово ({label})"
        if permanent_url:
            caption += f"\n🔗 Постоянная ссылка: {permanent_url}"

        # 4. Try send_video
        try:
            with open(send_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                )
            try:
                await progress_query.delete_message()
            except Exception:
                pass
            return
        except Exception as e:
            logger.warning(
                f"[user:{user_id}] send_video failed ({e}), falling back to send_document"
            )

        # 5. Fallback: send_document — на мобильном даёт явную кнопку «Скачать»
        try:
            with open(send_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    caption=caption,
                    filename=os.path.basename(send_path),
                )
            try:
                await progress_query.delete_message()
            except Exception:
                pass
            return
        except Exception as e:
            logger.warning(f"[user:{user_id}] send_document failed ({e}), falling back to URL")

        # 6. Final fallback — send our stable URL
        if permanent_url:
            try:
                await progress_query.edit_message_text(
                    f"✅ Готово ({label})\n"
                    f"Файл слишком большой для отправки в Telegram.\n"
                    f"🔗 Постоянная ссылка (не протухает):\n{permanent_url}"
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ Готово ({label})\n"
                        f"🔗 Скачать: {permanent_url}"
                    ),
                )
        else:
            try:
                await progress_query.edit_message_text(
                    f"⚠️ Готово ({label}), но не получилось ни отправить файл, "
                    f"ни сохранить локальную копию.\n"
                    f"Прямая ссылка (быстро протухнет):\n{video_url}"
                )
            except Exception:
                pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --- HeyGen v3 API wrappers (sync, called via asyncio.to_thread) ---------

def _heygen_v3_image_to_video(image_url: str, audio_url: str, avatar_version: str = "v4") -> str:
    """POST /v3/videos with type:image. Returns video_id.

    ⚠️ Image-to-Video — это технология Avatar IV. Для произвольного фото
    (без регистрации avatar_id через `/v3/avatars`) **только Avatar IV**
    может анимировать — Avatar 3 работает только с pre-trained look-ами.

    Параметр ``avatar_version`` сохранён для совместимости с caller'ами,
    но по факту игнорируется внутри payload. HeyGen v3 endpoint **не
    принимает** ``use_avatar_iv_model`` — поле помечено `Extra inputs are
    not permitted` (HeyGen v3 400 invalid_parameter, проверено 5 мая 2026).

    Цена для каждого Image-to-Video ролика — Avatar IV $0.05/sec @ 1080p
    (см. reference_heygen_api_v3.md, секция Self-Serve Pricing).
    """
    if not _heygen_api_key:
        raise RuntimeError("HEYGEN_API_KEY не настроен")

    headers = {
        "x-api-key": _heygen_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "type": "image",
        "image": {"type": "url", "url": image_url},
        "audio_url": audio_url,
        "title": f"image_to_video {avatar_version}",
        "resolution": "1080p",
        "aspect_ratio": "9:16",
        # use_avatar_iv_model: УБРАН — v3 endpoint не принимает этот параметр.
        # Image-to-Video всегда использует Avatar IV (это его технология).
    }

    resp = httpx.post(
        "https://api.heygen.com/v3/videos",
        headers=headers,
        json=payload,
        timeout=30,
    )
    data = resp.json()
    if resp.status_code >= 400:
        # Surface the HeyGen error message to caller
        err = data.get("error") or data.get("message") or data
        raise RuntimeError(f"HeyGen v3 {resp.status_code}: {err}")
    inner = data.get("data") or data
    video_id = inner.get("video_id") or inner.get("id")
    if not video_id:
        raise RuntimeError(f"HeyGen v3 returned no video_id: {data}")
    return video_id


def _heygen_v3_check_status(video_id: str) -> dict:
    """GET /v3/videos/{video_id}. Returns dict with status, video_url, duration, error."""
    if not _heygen_api_key:
        raise RuntimeError("HEYGEN_API_KEY не настроен")

    headers = {"x-api-key": _heygen_api_key, "Accept": "application/json"}
    resp = httpx.get(
        f"https://api.heygen.com/v3/videos/{video_id}",
        headers=headers,
        timeout=15,
    )
    data = resp.json()
    inner = data.get("data") or data
    return {
        "status": inner.get("status"),
        "video_url": inner.get("video_url"),
        "duration": inner.get("duration"),
        "failure_message": inner.get("failure_message"),
        "error": inner.get("error"),
    }


# --- Registration ---------------------------------------------------------

def register_heygen_test_handlers(
    app: Application,
    pending: dict,
    save_pending_fn: Callable,
    save_media_fn: Callable,
    save_image_fn: Callable,
    heygen_api_key: str,
):
    """Wire /heygen_test command + version picker callback into the bot."""
    global _pending, _save_pending, _save_media, _save_image, _heygen_api_key
    _pending = pending
    _save_pending = save_pending_fn
    _save_media = save_media_fn
    _save_image = save_image_fn
    _heygen_api_key = heygen_api_key or ""

    app.add_handler(CommandHandler("heygen_test", heygen_test_command))
    app.add_handler(
        CallbackQueryHandler(_on_pick, pattern=r"^heygen_test:(v3|v4|cancel)$")
    )
    logger.info("heygen_test_handlers registered: /heygen_test, heygen_test:* callbacks")
