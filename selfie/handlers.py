"""selfie.handlers — Telegram-flow для selfie-pipeline с шагом редактирования
транскрипции и выбором фоновой музыки.

Архитектура: модуль зависит от bot.py (pending, _save_pending, ASSETS_DIR, logger).
Эти зависимости инжектируются через init() при старте бота — циклический импорт
исключён.

State machine (значения pending[user_id]["state"]):
  - "selfie_waiting_video"   → ждёт видео (handled by process_video)
  - "selfie_text_review"     → показал текст, ждёт callback (handle_text_review_callback)
  - "selfie_text_editing"    → ждёт текстовое сообщение с правкой (handle_text_edit_message)
  - "selfie_music_picking"   → burn готов, юзер выбирает музыку (handle_music_callback)
  - "selfie_cover_picking"   → музыка готова, юзер выбирает обложку (handle_cover_callback)
  - "selfie_cover_uploading" → ждёт photo для обложки (handle_cover_photo_message)
  - "selfie_waiting_title"   → всё готово → control bot.py (его существующий код)

Callbacks "selfie_text:*":
  - "ok" / "confirm"          → burn subtitles → music_picking
  - "edit" / "edit_again"     → state selfie_text_editing
  - "cancel_edit"             → возврат в selfie_text_review

Callbacks "selfie_music:*":
  - "cat:<category>"          → pick random track из категории → mix → preview
  - "reroll:<category>"       → другой случайный трек той же категории → mix → preview
  - "back"                    → вернуться к выбору категории
  - "skip"                    → пропустить музыку → переход к COVER picker
  - "accept"                  → подтвердить текущий микс → переход к COVER picker

Callbacks "selfie_cover:*":
  - "frame:start|mid|end"     → ffmpeg snapshot timestamp'а → к названию
  - "upload"                  → state selfie_cover_uploading
  - "library"                 → 6 случайных из broll-library/photos
  - "lib_pick:<id>"           → выбрать конкретное фото → к названию
  - "lib_reroll"              → ещё 6 (exclude уже показанные)
  - "back"                    → вернуться к picker
  - "skip"                    → дефолт = первый кадр → к названию
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from selfie import cover as selfie_cover
from selfie import music as selfie_music
from selfie.edit import apply_user_edits
from selfie.transcribe import transcribe


# ── Module-level injected state ─────────────────────────────────────────────
# Set via init() at bot startup. NOT thread-safe but бот single-process.
_PENDING: dict | None = None
_SAVE_PENDING = None
_ASSETS_DIR: Path | None = None
_LOGGER = None
_SELFIE_FINALIZE = None  # legacy bot.py finalizer


def init(
    pending: dict,
    save_pending,
    assets_dir: Path,
    logger,
    selfie_finalize=None,
) -> None:
    """Inject bot.py dependencies. Call once at startup."""
    global _PENDING, _SAVE_PENDING, _ASSETS_DIR, _LOGGER, _SELFIE_FINALIZE
    _PENDING = pending
    _SAVE_PENDING = save_pending
    _ASSETS_DIR = assets_dir
    _LOGGER = logger
    _SELFIE_FINALIZE = selfie_finalize


# ── Pure helpers (unit-tested) ──────────────────────────────────────────────


def truncate_for_preview(text: str, max_chars: int) -> str:
    """Обрезать текст для preview, добавить ellipsis если урезали."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def build_review_message(transcript: str, edited: bool = False) -> str:
    """Собрать текст сообщения для шага review.

    Args:
        transcript: текущая транскрипция (после возможной правки).
        edited: True если уже была правка (меняем заголовок).

    Returns:
        Готовая строка для send_message / edit_message_text. Гарантированно
        короче 4096 символов (TG-лимит) — длинная транскрипция truncated.
    """
    # Резерв на статический текст: ~300 chars
    preview = truncate_for_preview(transcript, 3500)
    header = (
        "✏️ <b>Расшифровка обновлена</b>" if edited
        else "📝 <b>Расшифровка готова</b>"
    )
    hint = (
        "\n\n<i>Проверь текст. Если есть ошибки в названиях AI-инструментов "
        "(например, «Джеминай» → «Gemini») — нажми «✏️ Редактировать». "
        "Если всё ок — нажми «✅ Использовать как есть».</i>"
    )
    return f"{header}\n\n{preview}{hint}"


def detect_text_unchanged(original: str, edited: str) -> bool:
    """Юзер прислал текст идентичный оригиналу (с учётом нормализации пробелов).

    Используется чтобы не делать лишнюю работу если юзер случайно нажал
    «Редактировать», скопировал текст и вернул без правок.
    """
    return original.split() == edited.split()


# ── Telegram-flow ───────────────────────────────────────────────────────────


def _review_keyboard(can_use_as_is: bool = True) -> InlineKeyboardMarkup:
    """Кнопки шага review."""
    rows = [[InlineKeyboardButton("✏️ Редактировать", callback_data="selfie_text:edit")]]
    if can_use_as_is:
        rows.append([InlineKeyboardButton("✅ Использовать как есть", callback_data="selfie_text:ok")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def _post_edit_keyboard() -> InlineKeyboardMarkup:
    """Кнопки после применения правки."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить и продолжить", callback_data="selfie_text:confirm")],
        [InlineKeyboardButton("✏️ Ещё правка", callback_data="selfie_text:edit_again")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])


def _editing_keyboard() -> InlineKeyboardMarkup:
    """Кнопки во время editing — даём возможность отменить."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Отмена правки", callback_data="selfie_text:cancel_edit")],
    ])


async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принять видео от юзера в state 'selfie_waiting_video':
    скачать → audio → transcribe (с brand biasing) → показать review с кнопками.

    Не запускает burn subtitles — это произойдёт после подтверждения текста.
    """
    user_id = update.effective_user.id
    video_file = update.message.video or update.message.document
    if not video_file or not (
        update.message.video
        or (getattr(video_file, "mime_type", None) and video_file.mime_type.startswith("video/"))
    ):
        await update.message.reply_text("Отправь видеофайл (MP4). Жду видео, снятое на телефон.")
        return

    msg = await update.message.reply_text("📥 Загружаю видео...")
    try:
        tg_file = await context.bot.get_file(video_file.file_id)
        selfie_tmp = Path(tempfile.mkdtemp(prefix="selfie_"))
        source_path = selfie_tmp / "source.mp4"
        await tg_file.download_to_drive(str(source_path))
        file_size = source_path.stat().st_size / 1024 / 1024
        _LOGGER.info(f"[selfie] Source video downloaded: {file_size:.1f} MB")

        await msg.edit_text("🎙 Расшифровываю речь (с подсказкой AI-брендов)...")

        # Extract audio
        audio_tmp = selfie_tmp / "_tmp_audio.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(source_path),
             "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
             str(audio_tmp)],
            capture_output=True, text=True, timeout=120,
        )

        # Transcribe with brand biasing (selfie.transcribe)
        words = await asyncio.to_thread(transcribe, str(audio_tmp), language="ru")
        transcript_text = " ".join(w["word"] for w in words) if words else ""
        _LOGGER.info(f"[selfie] Transcribed: {len(words)} words, {len(transcript_text)} chars")

        # Cleanup temp audio
        try:
            audio_tmp.unlink()
        except OSError:
            pass

        if not transcript_text.strip():
            await msg.edit_text(
                "Не удалось распознать речь в видео. "
                "Попробуй отправить другое видео с чёткой речью."
            )
            _PENDING[user_id]["state"] = "selfie_waiting_video"
            _SAVE_PENDING(_PENDING)
            return

        # Save to pending — БЕЗ burn subtitles, ждём подтверждения текста
        _PENDING[user_id] = {
            "state": "selfie_text_review",
            "selfie_tmp_dir": str(selfie_tmp),
            "selfie_source": str(source_path),
            "selfie_words": words,
            "selfie_transcript": transcript_text,
            "selfie_orig_transcript": transcript_text,  # сохраняем оригинал для diff
        }
        _SAVE_PENDING(_PENDING)

        # Show review with buttons
        await msg.edit_text(
            build_review_message(transcript_text, edited=False),
            reply_markup=_review_keyboard(can_use_as_is=True),
            parse_mode="HTML",
        )
    except Exception as e:
        _LOGGER.error(f"[selfie] process_video error: {e}", exc_info=True)
        _PENDING[user_id]["state"] = "selfie_waiting_video"
        _SAVE_PENDING(_PENDING)
        await msg.edit_text(
            f"Ошибка обработки видео: {e}\n\n"
            "Попробуй отправить другое видео."
        )


async def handle_text_review_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Обработать selfie_text:* callbacks. Возвращает True если callback обработан
    (для интеграции с диспетчером в bot.py)."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("selfie_text:"):
        return False

    user_id = update.effective_user.id
    action = query.data.split(":", 1)[1]
    await query.answer()

    data = _PENDING.get(user_id)
    if not data:
        await query.edit_message_text("⚠️ Сессия selfie не найдена. Начни заново через /selfie.")
        return True

    if action == "ok":
        # Используем текст как есть, не было правок — переходим к burn + title
        await _burn_and_request_title(query, context, user_id, edited=False)
        return True

    if action == "confirm":
        # Подтвердил после правки — burn + title
        await _burn_and_request_title(query, context, user_id, edited=True)
        return True

    if action == "edit" or action == "edit_again":
        # Переходим в state ожидания нового текста
        data["state"] = "selfie_text_editing"
        _SAVE_PENDING(_PENDING)
        current = data.get("selfie_transcript", "")
        await query.edit_message_text(
            "✏️ <b>Пришли исправленный текст ответом</b>\n\n"
            "Правь только орфографию слов (Whisper иногда искажает названия). "
            "<b>Количество слов должно остаться тем же.</b>\n\n"
            f"<i>Текущая транскрипция:</i>\n{truncate_for_preview(current, 3000)}",
            reply_markup=_editing_keyboard(),
            parse_mode="HTML",
        )
        return True

    if action == "cancel_edit":
        # Отмена режима правки — возвращаемся к review с теми же кнопками
        data["state"] = "selfie_text_review"
        _SAVE_PENDING(_PENDING)
        current = data.get("selfie_transcript", "")
        await query.edit_message_text(
            build_review_message(current, edited=(current != data.get("selfie_orig_transcript", current))),
            reply_markup=_review_keyboard(),
            parse_mode="HTML",
        )
        return True

    return False


async def handle_text_edit_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Обработать текстовое сообщение в state 'selfie_text_editing' — это новая
    редакция транскрипции. Возвращает True если обработали."""
    user_id = update.effective_user.id
    data = _PENDING.get(user_id)
    if not data or data.get("state") != "selfie_text_editing":
        return False

    new_text = (update.message.text or "").strip()
    if not new_text:
        await update.message.reply_text("Пришли исправленный текст (не пустое сообщение).")
        return True

    orig_words: list[dict] = data.get("selfie_words", [])
    current_transcript: str = data.get("selfie_transcript", "")

    # Если идентично текущему — просто возвращаемся в review без warning
    if detect_text_unchanged(current_transcript, new_text):
        data["state"] = "selfie_text_review"
        _SAVE_PENDING(_PENDING)
        await update.message.reply_text(
            "Текст не изменился. Возвращаюсь к выбору.",
        )
        await update.message.reply_text(
            build_review_message(current_transcript, edited=False),
            reply_markup=_review_keyboard(),
            parse_mode="HTML",
        )
        return True

    # Apply edit
    new_words, warning = apply_user_edits(orig_words, new_text)

    if warning:
        # Несоответствие кол-ва слов — показываем warning, остаёмся в editing
        await update.message.reply_text(
            warning,
            reply_markup=_editing_keyboard(),
        )
        return True

    # Успех — сохраняем правку и показываем результат с кнопкой подтверждения
    data["selfie_words"] = new_words
    new_transcript = " ".join(w["word"] for w in new_words)
    data["selfie_transcript"] = new_transcript
    data["state"] = "selfie_text_review"  # будем ждать confirm
    _SAVE_PENDING(_PENDING)
    _LOGGER.info(f"[selfie] User edited transcript: {len(new_words)} words")

    await update.message.reply_text(
        build_review_message(new_transcript, edited=True),
        reply_markup=_post_edit_keyboard(),
        parse_mode="HTML",
    )
    return True


# ── Internal: burn subtitles after text confirmation ────────────────────────


async def _burn_and_request_title(query, context, user_id: int, edited: bool) -> None:
    """После подтверждения текста: burn subtitles → переход в music_picking.

    После burn state = "selfie_music_picking" — юзер выбирает фоновую музыку
    (или жмёт «Без музыки»). Финальное название запрашивается только после
    подтверждения музыки (или skip) в handle_music_callback._proceed_to_title.
    """
    data = _PENDING[user_id]
    words: list[dict] = data["selfie_words"]
    selfie_tmp = Path(data["selfie_tmp_dir"])
    source_path = Path(data["selfie_source"])
    transcript_text = data["selfie_transcript"]

    status = await query.edit_message_text(
        "🎬 Накладываю субтитры..." + (" (после правки)" if edited else "")
    )

    try:
        # Lazy import — heavy module
        from subtitle_burner import generate_ass, burn_subtitles, DEFAULT_FONT, DEFAULT_FONTSIZE, DEFAULT_MARGIN_V

        font_dir = _ASSETS_DIR / "fonts"
        font_dir_path = font_dir if font_dir.exists() else None

        ass_path = selfie_tmp / "_tmp_subs.ass"
        await asyncio.to_thread(
            generate_ass,
            words, ass_path,
            font=DEFAULT_FONT,
            fontsize=DEFAULT_FONTSIZE,
            uppercase=True,
            margin_v=DEFAULT_MARGIN_V,
        )

        subtitled_path = selfie_tmp / "subtitled.mp4"
        await asyncio.to_thread(
            burn_subtitles,
            source_path, ass_path,
            output_path=subtitled_path,
            font_dir=font_dir_path,
        )
        try:
            ass_path.unlink()
        except OSError:
            pass
        _LOGGER.info(f"[selfie] Subtitles burned: {subtitled_path.stat().st_size / 1024 / 1024:.1f} MB")

        # Save to pending — переходим в music_picking
        # selfie_subtitled — базовое видео без музыки (исходник для микса)
        # selfie_final — то, что пойдёт в bot.py финализер (subtitled или микс)
        _PENDING[user_id] = {
            "state": "selfie_music_picking",
            "selfie_tmp_dir": str(selfie_tmp),
            "selfie_source": str(source_path),
            "selfie_subtitled": str(subtitled_path),  # без музыки
            "selfie_final": str(subtitled_path),       # пока совпадает; после mix перепишется
            "selfie_transcript": transcript_text,
            "selfie_edited": edited,
        }
        _SAVE_PENDING(_PENDING)

        edit_note = " ✏️ (после правки)" if edited else ""
        await status.edit_text(
            f"✅ Субтитры наложены!{edit_note}\n\n"
            f"{selfie_music.build_music_picker_message()}",
            reply_markup=selfie_music.category_keyboard(),
            parse_mode="HTML",
        )
    except Exception as e:
        _LOGGER.error(f"[selfie] _burn_and_request_title error: {e}", exc_info=True)
        _PENDING[user_id]["state"] = "selfie_waiting_video"
        _SAVE_PENDING(_PENDING)
        await status.edit_text(
            f"Ошибка наложения субтитров: {e}\n\n"
            "Попробуй отправить видео заново."
        )


# ── Music picking ───────────────────────────────────────────────────────────


def _category_label(cat: str) -> str:
    """Удобный label для category из tracks.json (fallback на сам ключ)."""
    for c in selfie_music.get_visible_categories():
        if c["cat"] == cat:
            return f"{c['emoji']} {c['label']}".strip()
    return cat


async def handle_music_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Обработать selfie_music:* callbacks. Возвращает True если обработан."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("selfie_music:"):
        return False

    user_id = update.effective_user.id
    parts = query.data.split(":", 2)  # ["selfie_music", action, optional arg]
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else None
    await query.answer()

    data = _PENDING.get(user_id)
    if not data or data.get("state") != "selfie_music_picking":
        await query.edit_message_text(
            "⚠️ Сессия selfie не найдена или закончилась. Начни заново через /selfie."
        )
        return True

    if action == "cat":
        # Юзер выбрал категорию → подбираем случайный трек и миксуем
        await _pick_and_mix(query, context, user_id, category=arg, exclude_id=None)
        return True

    if action == "reroll":
        # Другой случайный трек той же категории (exclude текущий)
        picked = data.get("selfie_picked_music") or {}
        exclude = picked.get("track_id")
        await _pick_and_mix(query, context, user_id, category=arg, exclude_id=exclude)
        return True

    if action == "back":
        # Возврат к выбору категории — финал тут не миксованный, сбрасываем
        data["selfie_final"] = data.get("selfie_subtitled", data.get("selfie_final"))
        data.pop("selfie_picked_music", None)
        _SAVE_PENDING(_PENDING)
        await query.edit_message_text(
            f"✅ Субтитры наложены.\n\n{selfie_music.build_music_picker_message()}",
            reply_markup=selfie_music.category_keyboard(),
            parse_mode="HTML",
        )
        return True

    if action == "skip":
        # Без музыки — selfie_final остаётся subtitled.mp4
        data["selfie_final"] = data.get("selfie_subtitled", data.get("selfie_final"))
        data.pop("selfie_picked_music", None)
        _SAVE_PENDING(_PENDING)
        _LOGGER.info(f"[selfie] User skipped music for user {user_id}")
        await _proceed_to_cover_picker(query, context, user_id, music_note="без музыки")
        return True

    if action == "accept":
        # Подтвердил текущий микс → к обложке
        picked = data.get("selfie_picked_music") or {}
        track_id = picked.get("track_id", "?")
        _LOGGER.info(f"[selfie] User accepted music {track_id} for user {user_id}")
        note = f"музыка: {track_id}" if track_id != "?" else "с музыкой"
        await _proceed_to_cover_picker(query, context, user_id, music_note=note)
        return True

    return False


async def _pick_and_mix(
    query, context, user_id: int, category: str | None, exclude_id: str | None
) -> None:
    """Выбрать случайный трек категории, смикшировать в subtitled.mp4, показать preview."""
    if not category:
        await query.edit_message_text(
            "⚠️ Не указана категория. Возврат к выбору.",
            reply_markup=selfie_music.category_keyboard(),
        )
        return

    track = selfie_music.pick_random_track(category, exclude_id=exclude_id)
    if not track:
        await query.edit_message_text(
            f"⚠️ В категории «{category}» нет треков. Выбери другую:",
            reply_markup=selfie_music.category_keyboard(),
            parse_mode="HTML",
        )
        return

    data = _PENDING[user_id]
    selfie_tmp = Path(data["selfie_tmp_dir"])
    subtitled_path = Path(data["selfie_subtitled"])  # источник для микса
    mixed_path = selfie_tmp / "subtitled_with_music.mp4"

    status = await query.edit_message_text(
        f"🎵 Подбираю трек из «{_category_label(category)}»...\n"
        f"<code>{track['id']}</code> · {track.get('duration', 0):.0f}s\n\n"
        f"🎬 Микширую (ducking)...",
        parse_mode="HTML",
    )

    try:
        ok = await asyncio.to_thread(
            selfie_music.mix_into_video,
            str(subtitled_path),
            track["file"],
            str(mixed_path),
        )
        if not ok or not mixed_path.exists():
            raise RuntimeError("ffmpeg mix вернул False или не создал файл")

        size_mb = mixed_path.stat().st_size / 1024 / 1024
        _LOGGER.info(f"[selfie] Music mixed: {track['id']} -> {size_mb:.1f} MB")

        data["selfie_picked_music"] = {
            "category": category,
            "track_id": track["id"],
            "track_file": track["file"],
        }
        data["selfie_final"] = str(mixed_path)
        _SAVE_PENDING(_PENDING)

        cat_label = next(
            (f"{c['emoji']} {c['label']}".strip()
             for c in selfie_music.get_visible_categories() if c["cat"] == category),
            category,
        )
        await status.edit_text(
            selfie_music.build_picked_message(cat_label, track, video_size_mb=size_mb),
            reply_markup=selfie_music.picked_keyboard(category),
            parse_mode="HTML",
        )
    except Exception as e:
        _LOGGER.error(f"[selfie] mix error: {e}", exc_info=True)
        await status.edit_text(
            f"⚠️ Не получилось смикшировать ({e}). Попробуй другой трек или жми «Без музыки».",
            reply_markup=selfie_music.picked_keyboard(category),
        )


async def _proceed_to_cover_picker(query, context, user_id: int, music_note: str) -> None:
    """После выбора музыки (или skip) — переходим к выбору обложки."""
    data = _PENDING[user_id]
    data["state"] = "selfie_cover_picking"
    data["selfie_music_note"] = music_note  # запомним для финального сообщения
    # Reset shown library ids (для reroll)
    data["selfie_cover_shown_lib_ids"] = []
    _SAVE_PENDING(_PENDING)

    await query.edit_message_text(
        f"✅ Видео готово ({music_note}).\n\n{selfie_cover.build_picker_message()}",
        reply_markup=selfie_cover.cover_picker_keyboard(),
        parse_mode="HTML",
    )


# ── Cover picking ───────────────────────────────────────────────────────────


async def handle_cover_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Обработать selfie_cover:* callbacks. Возвращает True если обработан."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("selfie_cover:"):
        return False

    user_id = update.effective_user.id
    parts = query.data.split(":", 2)  # ["selfie_cover", action, optional arg]
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else None
    await query.answer()

    data = _PENDING.get(user_id)
    if not data or data.get("state") not in ("selfie_cover_picking", "selfie_cover_uploading"):
        await query.edit_message_text(
            "⚠️ Сессия selfie не найдена или закончилась. Начни заново через /selfie."
        )
        return True

    if action == "frame":
        await _pick_frame_cover(query, context, user_id, which=arg or "start")
        return True

    if action == "skip":
        # Дефолт: первый кадр
        await _pick_frame_cover(query, context, user_id, which="start", note="первый кадр (default)")
        return True

    if action == "upload":
        data["state"] = "selfie_cover_uploading"
        _SAVE_PENDING(_PENDING)
        await query.edit_message_text(
            selfie_cover.build_upload_prompt_message(),
            reply_markup=selfie_cover.upload_keyboard(),
            parse_mode="HTML",
        )
        return True

    if action == "library":
        await _show_library(query, user_id, reroll=False)
        return True

    if action == "lib_reroll":
        await _show_library(query, user_id, reroll=True)
        return True

    if action == "lib_pick" and arg:
        await _pick_library_cover(query, context, user_id, photo_id=arg)
        return True

    if action == "back":
        # Вернуться к picker
        data["state"] = "selfie_cover_picking"
        _SAVE_PENDING(_PENDING)
        music_note = data.get("selfie_music_note", "")
        await query.edit_message_text(
            f"✅ Видео готово ({music_note}).\n\n{selfie_cover.build_picker_message()}",
            reply_markup=selfie_cover.cover_picker_keyboard(),
            parse_mode="HTML",
        )
        return True

    return False


async def handle_cover_photo_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Обработать photo-сообщение в state selfie_cover_uploading.

    Возвращает True если обработали (для интеграции с bot.py photo handler).
    """
    user_id = update.effective_user.id
    data = _PENDING.get(user_id)
    if not data or data.get("state") != "selfie_cover_uploading":
        return False

    photo = update.message.photo
    if not photo:
        # Не фото — игнорируем (юзер может прислать текст по ошибке)
        await update.message.reply_text(
            "Жду фото (не текст). Или нажми «⬅️ Назад» в предыдущем сообщении."
        )
        return True

    # Самая большая версия фото
    best = photo[-1]
    selfie_tmp = Path(data["selfie_tmp_dir"])
    cover_path = selfie_tmp / "cover_uploaded.jpg"

    status_msg = await update.message.reply_text("📥 Сохраняю обложку...")

    try:
        tg_file = await context.bot.get_file(best.file_id)
        await tg_file.download_to_drive(str(cover_path))
        _LOGGER.info(f"[selfie] User uploaded cover: {cover_path.stat().st_size / 1024:.1f} KB")
        await status_msg.delete()
        await _finalize_with_cover(
            message_or_query=update.message,
            context=context,
            user_id=user_id,
            cover_path=cover_path,
            cover_note="загруженное фото",
        )
    except Exception as e:
        _LOGGER.error(f"[selfie] cover upload error: {e}", exc_info=True)
        await status_msg.edit_text(
            f"⚠️ Не получилось сохранить ({e}). Попробуй ещё раз или жми «⬅️ Назад»."
        )
    return True


async def _pick_frame_cover(query, context, user_id: int, which: str, note: str | None = None) -> None:
    """Извлечь кадр (start/mid/end) и финализировать."""
    data = _PENDING[user_id]
    selfie_tmp = Path(data["selfie_tmp_dir"])
    source_path = Path(data["selfie_source"])
    cover_path = selfie_tmp / f"cover_frame_{which}.jpg"

    duration = await asyncio.to_thread(selfie_cover.probe_video_duration, str(source_path))
    timestamps = selfie_cover.get_frame_timestamps(duration or 30.0)
    ts_map = {"start": timestamps[0], "mid": timestamps[1], "end": timestamps[2]}
    ts = ts_map.get(which, timestamps[0])

    status = await query.edit_message_text(
        f"🎞 Извлекаю кадр ({ts:.1f}s)..."
    )

    ok = await asyncio.to_thread(
        selfie_cover.extract_frame, str(source_path), ts, str(cover_path)
    )
    if not ok or not cover_path.exists():
        await status.edit_text(
            "⚠️ Не получилось извлечь кадр. Попробуй другой вариант:",
            reply_markup=selfie_cover.cover_picker_keyboard(),
        )
        return

    _LOGGER.info(f"[selfie] Frame cover extracted: {which} @ {ts:.1f}s -> {cover_path.stat().st_size / 1024:.1f} KB")
    if note is None:
        labels = {"start": "начало", "mid": "середина", "end": "финал"}
        note = f"кадр: {labels.get(which, which)} ({ts:.1f}s)"
    await _finalize_with_cover(
        message_or_query=query,
        context=context,
        user_id=user_id,
        cover_path=cover_path,
        cover_note=note,
    )


async def _show_library(query, user_id: int, reroll: bool) -> None:
    """Показать 6 случайных фото из библиотеки."""
    data = _PENDING[user_id]
    shown = data.get("selfie_cover_shown_lib_ids", [])
    exclude = shown if reroll else None
    sample = await asyncio.to_thread(
        selfie_cover.list_library_sample, 6, exclude
    )
    # Запоминаем показанные id для reroll
    if reroll:
        data["selfie_cover_shown_lib_ids"] = list(shown) + [s["id"] for s in sample]
    else:
        data["selfie_cover_shown_lib_ids"] = [s["id"] for s in sample]
    _SAVE_PENDING(_PENDING)

    await query.edit_message_text(
        selfie_cover.build_library_message(sample),
        reply_markup=selfie_cover.library_keyboard(sample),
        parse_mode="HTML",
    )


async def _pick_library_cover(query, context, user_id: int, photo_id: str) -> None:
    """Юзер выбрал фото из библиотеки → скопировать в tmp и финализировать."""
    import shutil as _shutil
    data = _PENDING[user_id]
    selfie_tmp = Path(data["selfie_tmp_dir"])

    src = selfie_cover.lookup_library_path(photo_id)
    if not src or not Path(src).exists():
        await query.edit_message_text(
            f"⚠️ Не нашёл фото <code>{photo_id}</code>. Выбери другое.",
            reply_markup=selfie_cover.cover_picker_keyboard(),
            parse_mode="HTML",
        )
        return

    # Сохраняем оригинальное расширение
    ext = Path(src).suffix.lower() or ".jpg"
    cover_path = selfie_tmp / f"cover_library{ext}"
    try:
        _shutil.copy2(src, str(cover_path))
        _LOGGER.info(f"[selfie] Library cover picked: {photo_id} ({cover_path.stat().st_size / 1024:.1f} KB)")
    except Exception as e:
        _LOGGER.error(f"[selfie] library copy error: {e}")
        await query.edit_message_text(
            f"⚠️ Ошибка копирования: {e}",
            reply_markup=selfie_cover.cover_picker_keyboard(),
        )
        return

    await _finalize_with_cover(
        message_or_query=query,
        context=context,
        user_id=user_id,
        cover_path=cover_path,
        cover_note=f"библиотека: {photo_id}",
    )


async def _finalize_with_cover(
    message_or_query, context, user_id: int, cover_path: Path, cover_note: str
) -> None:
    """Сохранить cover_path в pending и перейти в state selfie_waiting_title.

    Совместимо с существующим bot.py-flow: state = selfie_waiting_title,
    pending содержит selfie_subtitled = финальный файл (с музыкой или без)
    и selfie_cover = выбранная обложка.
    """
    data = _PENDING[user_id]
    selfie_tmp = Path(data["selfie_tmp_dir"])
    source_path = Path(data["selfie_source"])
    transcript_text = data.get("selfie_transcript", "")
    final_path = Path(data.get("selfie_final") or data.get("selfie_subtitled"))
    music_note = data.get("selfie_music_note", "без музыки")

    # Auto-title from first sentence
    first_sentence = transcript_text.split(".")[0].strip()[:80] if transcript_text else "Живое видео"
    if len(first_sentence) < 5:
        first_sentence = transcript_text[:80].strip()
    if not first_sentence:
        first_sentence = "Живое видео"

    _PENDING[user_id] = {
        "state": "selfie_waiting_title",
        "selfie_tmp_dir": str(selfie_tmp),
        "selfie_source": str(source_path),
        "selfie_subtitled": str(final_path),                # для legacy bot.py
        "selfie_subtitled_nomusic": data.get("selfie_subtitled", str(final_path)),
        "selfie_cover": str(cover_path),
        "selfie_transcript": transcript_text,
        "selfie_auto_title": first_sentence,
        "selfie_picked_music": data.get("selfie_picked_music"),
        "selfie_cover_note": cover_note,
    }
    _SAVE_PENDING(_PENDING)

    text = (
        f"✅ Готово (музыка: {music_note}; обложка: {cover_note}).\n\n"
        f"📝 Расшифровка:\n{truncate_for_preview(transcript_text, 500)}\n\n"
        f"———\n"
        f"Предлагаю название: «{first_sentence}»\n\n"
        f"Утверди или напиши своё название:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Утвердить название", callback_data="selfie_auto_title")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])

    # message_or_query: CallbackQuery → edit; Message → reply
    if hasattr(message_or_query, "edit_message_text"):
        # CallbackQuery
        await message_or_query.edit_message_text(text, reply_markup=kb)
    else:
        # Message
        await message_or_query.reply_text(text, reply_markup=kb)
