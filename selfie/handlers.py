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
import subprocess
import tempfile
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from selfie import broll_picker as selfie_broll
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
_TITLE_PICKER = None  # optional override of the default first-sentence title UI
_COVER_TEXT_STEP = None  # optional «текст на обложку?» step before title picker


def init(
    pending: dict,
    save_pending,
    assets_dir: Path,
    logger,
    selfie_finalize=None,
    title_picker=None,
    cover_text_step=None,
) -> None:
    """Inject bot.py dependencies. Call once at startup.

    Args:
        title_picker: optional async callable
            ``(message_or_query, context, user_id, transcript_text, first_sentence) -> None``
            invoked instead of the built-in "Утвердить простое название" UI after
            cover-picking. Lets the host bot replace the trivial first-sentence
            title with a richer picker (e.g. Claude-generated hooks).
        cover_text_step: optional async callable
            ``(message_or_query, context, user_id, cover_path, transcript) -> None``
            invoked AFTER cover photo is chosen — offers «текст на обложку?»
            (С текстом/Без). The host bot owns this (needs generate_cover +
            Claude). When done it calls the title_picker itself. If None —
            cover-pick goes straight to title_picker (no text overlay step).
    """
    global _PENDING, _SAVE_PENDING, _ASSETS_DIR, _LOGGER, _SELFIE_FINALIZE
    global _TITLE_PICKER, _COVER_TEXT_STEP
    _PENDING = pending
    _SAVE_PENDING = save_pending
    _ASSETS_DIR = assets_dir
    _LOGGER = logger
    _SELFIE_FINALIZE = selfie_finalize
    _TITLE_PICKER = title_picker
    _COVER_TEXT_STEP = cover_text_step


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


_VIDEO_EXTS = (".mov", ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mpeg", ".mpg")


def is_video_message(message) -> bool:
    """Видео ли сообщение: нативное video, ИЛИ документ с mime video/*, ИЛИ
    документ с видео-РАСШИРЕНИЕМ.

    Telegram Web часто шлёт .MOV как документ с ненадёжным mime (не video/*)
    → ловим по расширению имени файла, иначе бот молча дропает видео на /selfie
    (Артём, 8 июня 2026, IMG_1566.MOV из Telegram Web).
    """
    if getattr(message, "video", None):
        return True
    doc = getattr(message, "document", None)
    if not doc:
        return False
    mime = getattr(doc, "mime_type", None) or ""
    if mime.startswith("video/"):
        return True
    fname = (getattr(doc, "file_name", "") or "").lower()
    return fname.endswith(_VIDEO_EXTS)


async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принять видео от юзера в state 'selfie_waiting_video':
    скачать → audio → transcribe (с brand biasing) → показать review с кнопками.

    Не запускает burn subtitles — это произойдёт после подтверждения текста.
    """
    user_id = update.effective_user.id
    video_file = update.message.video or update.message.document
    if not is_video_message(update.message):
        await update.message.reply_text("Отправь видеофайл (MP4/MOV). Жду видео, снятое на телефон.")
        return

    msg = await update.message.reply_text("📥 Загружаю видео...")
    try:
        # Большие таймауты: видео с телефона 13-50 МБ не успевало скачаться за
        # дефолтные ~5с PTB → telegram.error.TimedOut (Артём 8 июня).
        tg_file = await context.bot.get_file(
            video_file.file_id, read_timeout=60, connect_timeout=30,
        )
        selfie_tmp = Path(tempfile.mkdtemp(prefix="selfie_"))
        source_path = selfie_tmp / "source.mp4"
        await tg_file.download_to_drive(
            str(source_path),
            read_timeout=180, write_timeout=180, connect_timeout=30,
        )
        file_size = source_path.stat().st_size / 1024 / 1024
        _LOGGER.info(f"[selfie] Source video downloaded: {file_size:.1f} MB")

        await msg.edit_text("🎙 Расшифровываю речь...")

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
        # Текст ОК → спрашиваем про монтаж перед прожигом.
        await _show_montage_choice(query, user_id, edited=False)
        return True

    if action == "confirm":
        # Подтвердил после правки → спрашиваем про монтаж.
        await _show_montage_choice(query, user_id, edited=True)
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


# ── Montage choice (с монтажом / без) перед прожигом ─────────────────────────


async def _show_montage_choice(query, user_id: int, edited: bool) -> None:
    """Спросить про динамичный монтаж перед burn. Запоминаем edited-флаг."""
    data = _PENDING[user_id]
    data["state"] = "selfie_montage_choice"
    data["selfie_montage_edited"] = edited
    _SAVE_PENDING(_PENDING)
    await query.edit_message_text(
        "🎬 <b>Динамичный монтаж?</b>\n\n"
        "Меняю крупность по ходу речи (плавные наезды/отъезды на лицо) — "
        "статичное селфи становится живее, как в интервью.\n\n"
        "Или оставить как снято (без монтажа).",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 С монтажом", callback_data="selfie_montage:on")],
            [InlineKeyboardButton("➡️ Без монтажа (как снято)", callback_data="selfie_montage:off")],
        ]),
        parse_mode="HTML",
    )


async def handle_montage_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Обработать selfie_montage:on|off — выбор динамичного монтажа.
    Возвращает True если обработан."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("selfie_montage:"):
        return False

    user_id = update.effective_user.id
    action = query.data.split(":", 1)[1]
    await query.answer()

    data = _PENDING.get(user_id)
    if not data or data.get("state") != "selfie_montage_choice":
        await query.edit_message_text(
            "⚠️ Сессия selfie не найдена или закончилась. Начни заново через /selfie."
        )
        return True

    data["selfie_punch_in"] = (action == "on")
    edited = bool(data.get("selfie_montage_edited", False))
    _SAVE_PENDING(_PENDING)
    _LOGGER.info(f"[selfie] Montage choice: punch_in={data['selfie_punch_in']}")
    await _burn_and_request_title(query, context, user_id, edited=edited)
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

        # ── Punch-in монтаж (опц.) ДО прожига субтитров ──────────────────────
        # Режем по границам предложений (Whisper-таймкоды) + меняем крупность
        # (100/108/114%, якорь на лицо) → динамика в статичном селфи. Субтитры
        # жгутся ПОВЕРХ уже смонтированного, поэтому остаются на месте.
        # Длительность не меняется → ASS-тайминги валидны. Env SELFIE_PUNCH_IN=1.
        burn_input = source_path
        from selfie import punch_in as _pi
        # Per-video выбор кнопкой (selfie_punch_in). Если ключа нет (старый
        # flow) — фоллбэк на env SELFIE_PUNCH_IN.
        _want_punch = data.get("selfie_punch_in")
        if _want_punch is None:
            _want_punch = _pi.punch_in_enabled()
        if _want_punch:
            try:
                segments = _pi.plan_punch_in_segments(words)
                if len(segments) > 1:
                    await status.edit_text("🎬 Монтирую крупности (динамика кадра)...")
                    punched_path = selfie_tmp / "punched.mp4"
                    await asyncio.to_thread(
                        _pi.render_punch_in, source_path, segments, punched_path
                    )
                    burn_input = punched_path
                    _LOGGER.info(f"[selfie] Punch-in applied: {len(segments)} segments")
            except Exception as e:
                # Монтаж — не критичен; субтитры важнее. Fallback на source.
                _LOGGER.warning(f"[selfie] Punch-in failed, using source: {e}")
                burn_input = source_path
            await status.edit_text("🎬 Накладываю субтитры...")

        subtitled_path = selfie_tmp / "subtitled.mp4"
        await asyncio.to_thread(
            burn_subtitles,
            burn_input, ass_path,
            output_path=subtitled_path,
            font_dir=font_dir_path,
        )
        try:
            ass_path.unlink()
        except OSError:
            pass
        _LOGGER.info(f"[selfie] Subtitles burned: {subtitled_path.stat().st_size / 1024 / 1024:.1f} MB")

        # Save to pending — переходим в selfie_broll_offer (Pipeline 2 gate).
        # selfie_subtitled — базовое видео с субтитрами (без B-roll и без музыки)
        # selfie_final — то, что пойдёт в bot.py финализер (по умолчанию subtitled,
        # после assemble_auto_montage перепишется на final_auto.mp4, после mix
        # ещё раз — на final_with_music.mp4)
        _PENDING[user_id] = {
            "state": "selfie_broll_offer",
            "selfie_tmp_dir": str(selfie_tmp),
            "selfie_source": str(source_path),
            "selfie_subtitled": str(subtitled_path),  # с субтитрами, без B-roll
            "selfie_final": str(subtitled_path),       # пока совпадает
            "selfie_transcript": transcript_text,
            "selfie_edited": edited,
            "selfie_broll_items": [],                  # будем накапливать в picker
            "selfie_broll_shown_ids": {},              # reroll: ключи "<src>:<cat>"
        }
        _SAVE_PENDING(_PENDING)

        edit_note = " ✏️ (после правки)" if edited else ""
        await status.edit_text(
            f"✅ Субтитры наложены!{edit_note}\n\n"
            "🎬 Хочешь добавить B-roll-вставки в ролик (фото/видео поверх селфи)?",
            reply_markup=selfie_broll.build_offer_keyboard(),
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
        # Юзер выбрал категорию → шлём 3 трека на прослушивание (НЕ микс).
        # Юзер слушает в нативном Telegram audio-плеере и выбирает.
        await _send_track_previews(query, context, user_id, category=arg, exclude_ids=None)
        return True

    if action == "reroll":
        # «Ещё 3 трека» — exclude всех показанных в этой сессии.
        shown = data.get("selfie_music_shown_ids") or []
        await _send_track_previews(query, context, user_id, category=arg, exclude_ids=shown)
        return True

    if action == "pick":
        # Юзер выбрал конкретный трек после прослушивания.
        # arg = "<category>:<track_id>"
        if not arg or ":" not in arg:
            await query.edit_message_text("⚠️ Неверный формат выбора трека.")
            return True
        cat, track_id = arg.split(":", 1)
        await _mix_picked_track(query, context, user_id, category=cat, track_id=track_id)
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


async def _send_track_previews(
    query, context, user_id: int, category: str | None, exclude_ids: list[str] | None,
) -> None:
    """Отправить 3 трека из категории как audio-файлы для прослушивания.

    Каждый трек — отдельное audio-сообщение с inline-кнопкой «✅ Выбрать этот».
    Юзер слушает прямо в Telegram (нативный audio-плеер) и решает.
    После выбора — переход в _mix_picked_track.
    """
    if not category:
        await query.edit_message_text(
            "⚠️ Не указана категория. Возврат к выбору.",
            reply_markup=selfie_music.category_keyboard(),
        )
        return

    tracks = selfie_music.pick_n_tracks(category, n=3, exclude_ids=exclude_ids or [])
    if not tracks:
        await query.edit_message_text(
            f"⚠️ В категории «{_category_label(category)}» больше нет новых треков.",
            reply_markup=selfie_music.category_keyboard(),
            parse_mode="HTML",
        )
        return

    data = _PENDING[user_id]
    # Запоминаем показанные ID для reroll
    shown = data.get("selfie_music_shown_ids") or []
    new_shown = list({*shown, *(t["id"] for t in tracks)})
    data["selfie_music_shown_ids"] = new_shown
    # Счётчик batch'ей — нужен для уникальности текста при reroll (иначе
    # Telegram падает с "Message is not modified" если текст и кнопки те же).
    batch_n = (data.get("selfie_music_batch_n") or 0) + 1
    data["selfie_music_batch_n"] = batch_n
    _SAVE_PENDING(_PENDING)

    cat_label = _category_label(category)
    chat_id = query.message.chat_id

    # Сначала кратко обновляем исходное сообщение (откуда был callback) —
    # иначе на нём остаются устаревшие кнопки категории, и юзер может кликнуть
    # повторно. Текст с счётчиком партии — гарантирует уникальность для Telegram
    # (иначе reroll падает с "Message is not modified").
    batch_suffix = "" if batch_n == 1 else f" — партия #{batch_n}"
    try:
        await query.edit_message_text(
            f"🎵 <b>{cat_label}</b> — подбираю треки{batch_suffix}...",
            parse_mode="HTML",
        )
    except Exception as e:
        _LOGGER.debug(f"[selfie] intro edit skipped: {e}")

    # 1) Шлём 3 аудио-сообщения с кнопкой «✅ Выбрать этот» под каждым.
    for i, track in enumerate(tracks, 1):
        try:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                f"✅ Выбрать этот ({track['id']})",
                callback_data=f"selfie_music:pick:{category}:{track['id']}",
            )]])
            with open(track["file"], "rb") as audio_f:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_f,
                    title=f"{i}. {track['id']}",
                    performer=cat_label,
                    duration=int(track.get("duration", 0)),
                    reply_markup=kb,
                )
        except Exception as e:
            _LOGGER.error(f"[selfie] send_audio failed for {track['id']}: {e}")
            # Не критично, продолжаем остальные

    # 2) В конце шлём footer-сообщение с кнопками управления — оно остаётся
    # последним в ленте, юзер видит управление сразу после прослушивания.
    footer_text = (
        f"☝️ Послушай {len(tracks)} трека выше и выбери «✅» под нужным.\n\n"
        f"Если ни один не подошёл — «🔄 Ещё треки»"
        + (f" (партия #{batch_n}, всего показано {len(new_shown)})" if batch_n > 1 else "")
        + "."
    )
    footer_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё треки", callback_data=f"selfie_music:reroll:{category}")],
        [InlineKeyboardButton("⬅️ Сменить категорию", callback_data="selfie_music:back")],
        [InlineKeyboardButton("❌ Без музыки", callback_data="selfie_music:skip")],
    ])
    await context.bot.send_message(
        chat_id=chat_id, text=footer_text, reply_markup=footer_kb,
    )


async def _mix_picked_track(
    query, context, user_id: int, category: str, track_id: str,
) -> None:
    """Юзер выбрал конкретный track_id — миксуем его в видео и шлём превью."""
    # Найти track-dict по id (вместо random)
    track = None
    for t in selfie_music._list_tracks(category):
        if t.get("id") == track_id:
            track = t
            break
    if not track:
        await query.edit_message_text(
            f"⚠️ Трек {track_id} не найден. Выбери другую категорию.",
            reply_markup=selfie_music.category_keyboard(),
        )
        return

    data = _PENDING[user_id]
    selfie_tmp = Path(data["selfie_tmp_dir"])
    subtitled_path = Path(data["selfie_subtitled"])
    mixed_path = selfie_tmp / "subtitled_with_music.mp4"

    # Статус новым сообщением (не edit-ом кнопки выбора) — чтобы лента не прыгала.
    status = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"🎬 Микширую трек <code>{track['id']}</code> в видео...",
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
        _LOGGER.info(f"[selfie] Music mixed (pick): {track['id']} -> {size_mb:.1f} MB")

        data["selfie_picked_music"] = {
            "category": category,
            "track_id": track["id"],
            "track_file": track["file"],
        }
        data["selfie_final"] = str(mixed_path)
        _SAVE_PENDING(_PENDING)

        cat_label = _category_label(category)
        await status.edit_text(
            selfie_music.build_picked_message(cat_label, track, video_size_mb=size_mb),
            reply_markup=selfie_music.picked_keyboard(category),
            parse_mode="HTML",
        )
    except Exception as e:
        _LOGGER.error(f"[selfie] mix error: {e}", exc_info=True)
        await status.edit_text(
            f"⚠️ Не получилось смикшировать ({e}). Попробуй другой трек.",
            reply_markup=selfie_music.picked_keyboard(category),
        )


async def _pick_and_mix(
    query, context, user_id: int, category: str | None, exclude_id: str | None
) -> None:
    """LEGACY: оставлено для совместимости с picked_keyboard «🔄 Другой трек»
    (микс случайного без прослушивания). Новый flow — _send_track_previews +
    _mix_picked_track.

    Выбрать случайный трек категории, смикшировать в subtitled.mp4, показать preview."""
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


async def _show_broll_review(query, user_id: int) -> None:
    """«Моё выбранное» — обзор всего набора (фото+клипы) + удаление «🗑 N».
    Артём 9 июня: до «Готово» не было где посмотреть/убрать набор."""
    data = _PENDING.get(user_id) or {}
    items = data.get("selfie_broll_items", []) or []
    if not items:
        await query.edit_message_text(
            "📋 Пока ничего не выбрано.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к выбору", callback_data="selfie_broll:back")]]))
        return
    lines = [f"📋 Моё выбранное ({len(items)}):\n"]
    rm_row = []
    for n, it in enumerate(items, 1):
        kind = "📷 фото" if it.get("kind") == "image" else "🎞 видео"
        label = it.get("label") or ""
        if not label or (isinstance(label, str) and label.startswith("library/")):
            label = Path(it.get("source", "")).stem
        lines.append(f"{n}. {kind} — {label}")
        rm_row.append(InlineKeyboardButton(f"🗑 {n}", callback_data=f"selfie_broll:rm:{n - 1}"))
    lines.append("\nЖми «🗑 N» чтобы убрать. Потом «Готово».")
    rows = [rm_row[i:i + 4] for i in range(0, len(rm_row), 4)]
    rows.append([InlineKeyboardButton("➕ Добавить ещё", callback_data="selfie_broll:back")])
    rows.append([InlineKeyboardButton(
        f"✅ Готово ({len(items)} выбрано)", callback_data="selfie_broll:done")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def _show_broll_picker_screen(query_or_msg, user_id: int) -> None:
    """Re-render the main B-roll picker (sources + selected list)."""
    data = _PENDING[user_id]
    items = _items_from_pending(data)
    text = selfie_broll.build_picker_message(items)
    kb = selfie_broll.build_picker_keyboard(items)
    # Plain text — labels may contain underscores/asterisks that break Markdown.
    if hasattr(query_or_msg, "edit_message_text"):
        try:
            await query_or_msg.edit_message_text(text, reply_markup=kb)
        except Exception:
            # message may be a photo/video confirmation — can't edit text
            await query_or_msg.message.reply_text(text, reply_markup=kb)
    else:
        await query_or_msg.reply_text(text, reply_markup=kb)


def _selected_lib_ids(data: dict) -> set:
    """ID библиотечных файлов, уже выбранных в B-roll (по label 'library/<id>')."""
    out = set()
    for d in data.get("selfie_broll_items", []) or []:
        lbl = d.get("label") or ""
        if lbl.startswith("library/"):
            out.add(lbl[len("library/"):])
    return out


async def _send_broll_previews(context, chat_id, samples, kind: str) -> None:
    """Прислать ЛЁГКИЕ media-превью батча: фото — уменьшенными картинками, клипы
    — маленькими обрезанными видео (исходники тяжёлые → генерим превью).
    Генераторы превью — в selfie.broll_picker (переиспользуются менеджером библ.)."""
    from telegram import InputMediaPhoto, InputMediaVideo
    import shutil as _sh
    prev_dir = Path(tempfile.mkdtemp(prefix="broll_prev_"))
    try:
        async def _mk(i: int, s: dict):
            if kind == "image":
                return await asyncio.to_thread(
                    selfie_broll.make_image_preview, s["path"], str(prev_dir / f"p_{i}.jpg"))
            return await asyncio.to_thread(
                selfie_broll.make_clip_preview, s["path"], str(prev_dir / f"p_{i}.mp4"))

        results = await asyncio.gather(
            *[_mk(i, s) for i, s in enumerate(samples)], return_exceptions=True)
        previews = [r for r in results if isinstance(r, str) and r and Path(r).exists()]
        if not previews:
            _LOGGER.warning("[selfie/broll] preview: ни одного превью не сгенерилось")
            return

        media, handles = [], []
        try:
            for p in previews:
                f = open(p, "rb")
                handles.append(f)
                media.append(InputMediaPhoto(f) if kind == "image" else InputMediaVideo(f))
            if len(media) >= 2:
                await context.bot.send_media_group(chat_id=chat_id, media=media)
            else:
                if kind == "image":
                    await context.bot.send_photo(chat_id=chat_id, photo=handles[0])
                else:
                    await context.bot.send_video(chat_id=chat_id, video=handles[0])
        finally:
            for f in handles:
                try:
                    f.close()
                except Exception:
                    pass
    except Exception as e:
        _LOGGER.warning(f"[selfie/broll] preview send failed: {e}")
    finally:
        _sh.rmtree(prev_dir, ignore_errors=True)


async def _show_broll_category_samples(
    query, context, user_id: int, kind: str, category: str,
) -> None:
    """Прислать media-превью 6 файлов категории + клавиатуру мультивыбора (toggle)."""
    data = _PENDING.get(user_id) or {}
    src_tag = "photo" if kind == "image" else "clip"
    shown_key = f"{src_tag}:{category}"
    samples = await asyncio.to_thread(
        selfie_broll.list_library_sample, kind, category, 6, [],
    )
    label = selfie_broll._cat_label(category)
    icon = "📷" if kind == "image" else "🎞"
    if not samples:
        await query.edit_message_text(f"⚠️ В категории «{label}» пусто.")
        return
    data.setdefault("selfie_broll_shown_ids", {})
    data["selfie_broll_shown_ids"][shown_key] = [s["id"] for s in samples]
    data["selfie_broll_batch"] = {
        "kind": kind, "category": category,
        "items": [{"id": s["id"], "path": s["path"]} for s in samples],
    }
    _SAVE_PENDING(_PENDING)
    try:
        await query.edit_message_text(f"{icon} {label} — показываю превью…")
    except Exception:
        pass
    chat_id = query.message.chat_id
    await _send_broll_previews(context, chat_id, samples, kind)
    selected_ids = _selected_lib_ids(data)
    total = len(_items_from_pending(data))
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{icon} {label}: тапни номер — выбрать/снять. Можно несколько; "
            f"«⬅️ К категориям» — добрать из другой. "
            f"Выбрано: {total}/{selfie_broll.MAX_BROLL_ITEMS}."
        ),
        reply_markup=selfie_broll.build_toggle_keyboard(
            samples, kind, category, selected_ids, total),
    )


def _items_from_pending(data: dict) -> list:
    """Reconstruct list[BrollItem] from pending (stored as list[dict])."""
    raw = data.get("selfie_broll_items", []) or []
    out = []
    for r in raw:
        out.append(selfie_broll.BrollItem(
            kind=r["kind"],
            source=Path(r["source"]),
            label=r.get("label"),
        ))
    return out


def _store_item(data: dict, item) -> None:
    """Append a BrollItem to pending (serialised as dict)."""
    data.setdefault("selfie_broll_items", [])
    data["selfie_broll_items"].append({
        "kind": item.kind,
        "source": str(item.source),
        "label": item.label,
    })


def _cleanup_selfie_gen_dirs(data: dict) -> None:
    """Удалить временные папки Remotion-генерации (selfie_gen_*).

    Вызывается после сборки (клипы уже скопированы в project_dir) и на
    skip/cancel. Без этого /tmp подтекает на каждую AI-генерацию.
    """
    import shutil as _sh
    for d in (data.get("selfie_gen_dirs") or []):
        try:
            _sh.rmtree(d, ignore_errors=True)
        except Exception:
            pass
    data["selfie_gen_dirs"] = []


def _drop_gen_dir(data: dict, gen_dir) -> None:
    """Точечно удалить ОДНУ gen-папку (на failure/discard генерации) + убрать
    её из selfie_gen_dirs. rmtree безусловный (диск освобождаем в любом случае)."""
    import shutil as _sh
    _sh.rmtree(gen_dir, ignore_errors=True)
    try:
        data.get("selfie_gen_dirs", []).remove(str(gen_dir))
    except (ValueError, AttributeError):
        pass


AIVID_STALE_SEC = 900   # a live AI-video gen can't run longer than this; older key = stale


def _aivid_inflight(data: dict, now: float, stale_sec: float = AIVID_STALE_SEC) -> bool:
    """Is a paid AI-video generation currently in flight for this user?

    Guards against a double-paid start. Time-based: a key persisted to disk and
    orphaned by a bot restart goes stale instead of locking the user out forever.
    """
    entry = data.get("selfie_aivid_job_id")
    if not isinstance(entry, dict):
        return False
    ts = entry.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    return (now - ts) <= stale_sec


def _aivid_job_matches(data: dict, job_id: str) -> bool:
    """Does the in-pending AI-video job still belong to this generation?

    False if a newer aivid run replaced the key, or cancel/skip cleared it — in
    both cases this run's (already-paid) clips must NOT be applied.
    """
    entry = data.get("selfie_aivid_job_id")
    return isinstance(entry, dict) and entry.get("id") == job_id


async def handle_broll_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Обработать selfie_broll:* callbacks. Возвращает True если обработан."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("selfie_broll:"):
        return False

    user_id = update.effective_user.id
    parts = query.data.split(":", 3)  # ["selfie_broll", action, optional_arg, optional_arg2]
    action = parts[1] if len(parts) > 1 else ""
    await query.answer()

    data = _PENDING.get(user_id)
    if not data or data.get("state") not in (
        "selfie_broll_offer", "selfie_broll_picking",
        "selfie_broll_uploading_photo", "selfie_broll_uploading_video",
    ):
        await query.edit_message_text(
            "⚠️ Сессия selfie не найдена или закончилась. Начни заново через /selfie."
        )
        return True

    # ── Offer step ──────────────────────────────────────────────────────────
    if action == "add":
        data["state"] = "selfie_broll_picking"
        _SAVE_PENDING(_PENDING)
        await _show_broll_picker_screen(query, user_id)
        return True

    if action in ("skip", "cancel"):
        # No B-roll → straight to music picker (legacy Pipeline 1 flow).
        data["state"] = "selfie_music_picking"
        data["selfie_broll_items"] = []
        # Clear any in-flight aivid key so its result is discarded (not applied
        # from a dir _cleanup just rmtree'd → would crash on copy).
        data.pop("selfie_aivid_job_id", None)
        _cleanup_selfie_gen_dirs(data)  # снять tmp AI-генерации, если была
        _SAVE_PENDING(_PENDING)
        await query.edit_message_text(
            "➡️ Без B-roll, продолжаем.\n\n"
            f"{selfie_music.build_music_picker_message()}",
            reply_markup=selfie_music.category_keyboard(),
            parse_mode="HTML",
        )
        return True

    # ── Picker actions ──────────────────────────────────────────────────────
    # «🎨 Сгенерировать графику (AI)» → Remotion-движок (auto_broll) генерит
    # 6 динамических сцен ИЗ ТЕКСТА селфи. Тяжело (~3-7 мин, рендер на сервере,
    # Claude Code по подписке, свой _GEN_LOCK). Клипы кладём как обычные
    # video-B-roll items → дальше тот же assemble_auto_montage (broll_mode=real).
    if action == "gen":
        transcript = (data.get("selfie_edited") or data.get("selfie_transcript") or "").strip()
        items = _items_from_pending(data)
        free = selfie_broll.MAX_BROLL_ITEMS - len(items)
        chat_id = query.message.chat_id
        if not transcript:
            await query.edit_message_text("⚠️ Нет текста для генерации графики.")
            return True
        if free <= 0:
            await query.edit_message_text(
                f"Достигнут лимит {selfie_broll.MAX_BROLL_ITEMS} вставок — "
                "убери что-то, потом генерируй.",
                reply_markup=selfie_broll.build_picker_keyboard(items),
            )
            return True
        # Идентичность джоба: за 5-8 мин генерации юзер может перегенерить,
        # отменить сценарий или начать новый flow. Сверим job_id+state ПЕРЕД
        # применением результата (Critical 2 из GPT-ревью).
        import uuid
        job_id = uuid.uuid4().hex[:12]
        data["selfie_ai_job_id"] = job_id
        await query.edit_message_text(
            "🎨 Генерю динамическую графику из текста (Remotion)…\n"
            "Это ~3-7 минут — рендер на сервере. Дождись, пришлю результат."
        )
        clips: list = []
        gen_err = ""
        # Трекаем gen_dir в pending → почистим после сборки (prepare копирует
        # клипы в project_dir) и на skip/cancel. Иначе /tmp подтекает.
        gen_dir = Path(tempfile.mkdtemp(prefix=f"selfie_gen_{user_id}_"))
        data.setdefault("selfie_gen_dirs", []).append(str(gen_dir))
        _SAVE_PENDING(_PENDING)
        try:
            import auto_broll
            clips, _cost = await asyncio.to_thread(
                auto_broll.generate_auto_broll, transcript, gen_dir,
            )
        except Exception as e:
            _LOGGER.error(f"[selfie/gen] auto_broll failed: {e}", exc_info=True)
            clips = []
            gen_err = str(e)[:150]

        # Перечитать АКТУАЛЬНОЕ состояние — могло смениться за время генерации.
        cur = _PENDING.get(user_id) or {}
        _PICK_STATES = (
            "selfie_broll_offer", "selfie_broll_picking",
            "selfie_broll_uploading_photo", "selfie_broll_uploading_video",
        )
        if cur.get("selfie_ai_job_id") != job_id or cur.get("state") not in _PICK_STATES:
            # Флоу сменился / перегенерили / отменили — результат НЕ применяем.
            _drop_gen_dir(cur, gen_dir)
            _SAVE_PENDING(_PENDING)
            await context.bot.send_message(
                chat_id=chat_id,
                text="ℹ️ Сценарий изменился за время генерации — графику не применил.",
            )
            return True

        if not clips:
            _drop_gen_dir(cur, gen_dir)  # Fix 5: чистим tmp на failure
            _SAVE_PENDING(_PENDING)
            items = _items_from_pending(cur)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "❌ Не удалось сгенерировать графику. "
                    + (f"({gen_err}) " if gen_err else "")
                    + "Попробуй ещё раз или выбери B-roll из библиотеки.\n\n"
                    + selfie_broll.build_picker_message(items)
                ),
                reply_markup=selfie_broll.build_picker_keyboard(items),
            )
            return True

        # Пересчёт лимита по АКТУАЛЬНОМУ состоянию (Critical 2).
        items = _items_from_pending(cur)
        free_now = selfie_broll.MAX_BROLL_ITEMS - len(items)
        added = 0
        for clip in clips[:max(0, free_now)]:
            _store_item(cur, selfie_broll.BrollItem(
                kind="video", source=Path(clip), label=f"[AI] {Path(clip).stem}",
            ))
            added += 1
        cur.pop("selfie_ai_job_id", None)
        _SAVE_PENDING(_PENDING)
        items = _items_from_pending(cur)
        capped = "" if added >= len(clips) else (
            f" (лимит {selfie_broll.MAX_BROLL_ITEMS} — лишние не добавил)"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🎨 Готово — добавил {added} AI-сцен{capped}.\n\n"
                + selfie_broll.build_picker_message(items)
            ),
            reply_markup=selfie_broll.build_picker_keyboard(items),
        )
        return True

    # «🎬 AI-видео по сценарию» → Seedance генерит кинематографичные клипы
    # ИЗ ТЕКСТА селфи. Сначала спрашиваем длину 5/10с (как в /video).
    if action == "aivideo":
        import ai_video_broll
        items = _items_from_pending(data)
        free = selfie_broll.MAX_BROLL_ITEMS - len(items)
        if free < ai_video_broll.MIN_CLIPS:
            await query.edit_message_text(
                f"Для AI-видео нужно ≥{ai_video_broll.MIN_CLIPS} свободных слота "
                f"(сейчас {free}). Убери что-то из выбранного.",
                reply_markup=selfie_broll.build_picker_keyboard(items),
            )
            return True
        lo5, hi5 = ai_video_broll.estimate_cost_range(5)
        lo10, hi10 = ai_video_broll.estimate_cost_range(10)
        await query.edit_message_text(
            "🎬 AI-видео по сценарию — выбери длину клипа (2-4 клипа на ролик):\n\n"
            f"• 5 сек — для перебивок (~${lo5:.2f}-{hi5:.2f})\n"
            f"• 10 сек — виден целиком в полноэкранных сегментах (smart/fullscreen/pro/ИИ), "
            f"иначе обрежется (~${lo10:.2f}-{hi10:.2f})",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 5 сек", callback_data="selfie_broll:aivid:5"),
                 InlineKeyboardButton("🎬 10 сек", callback_data="selfie_broll:aivid:10")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="selfie_broll:back")],
            ]),
        )
        return True

    # selfie_broll:aivid:<5|10> — запуск Seedance с выбранной длиной. Структура
    # совпадает с «gen» (Remotion): тяжёлая генерация в to_thread, job_id-гард
    # против смены флоу, клипы кладём как обычные video-B-roll items.
    if action == "aivid":
        import ai_video_broll
        import uuid
        duration = 10 if (len(parts) > 2 and parts[2] == "10") else 5
        transcript = (data.get("selfie_edited") or data.get("selfie_transcript") or "").strip()
        items = _items_from_pending(data)
        free = selfie_broll.MAX_BROLL_ITEMS - len(items)
        chat_id = query.message.chat_id
        if not transcript:
            await query.edit_message_text("⚠️ Нет текста для генерации AI-видео.")
            return True
        if free < ai_video_broll.MIN_CLIPS:
            await query.edit_message_text(
                f"Для AI-видео нужно ≥{ai_video_broll.MIN_CLIPS} свободных слота (сейчас {free}).",
                reply_markup=selfie_broll.build_picker_keyboard(items),
            )
            return True
        # Busy-guard: read+set are synchronous (no await between) → atomic vs a
        # double-click. Timestamped so a restart-orphaned key goes stale, not locks.
        now = time.time()
        if _aivid_inflight(data, now):
            await context.bot.send_message(
                chat_id=chat_id, text="⏳ AI-видео уже генерится — дождись результата.")
            return True
        job_id = uuid.uuid4().hex[:12]
        data["selfie_aivid_job_id"] = {"id": job_id, "ts": now}
        max_clips = min(free, ai_video_broll.MAX_CLIPS)   # never plan/pay for more than fits
        await query.edit_message_text(
            f"🎬 Генерю AI-видео из текста (Seedance, ~{duration}с/клип)…\n"
            "Несколько минут — рендер в облаке. Дождись, пришлю результат."
        )
        clips: list = []
        cost = 0.0
        gen_err = ""
        gen_dir = Path(tempfile.mkdtemp(prefix=f"selfie_aivid_{user_id}_"))
        data.setdefault("selfie_gen_dirs", []).append(str(gen_dir))
        _SAVE_PENDING(_PENDING)
        try:
            clips, cost = await asyncio.to_thread(
                ai_video_broll.generate_ai_broll, transcript, gen_dir, None, duration, None, max_clips,
            )
        except Exception as e:
            _LOGGER.error(f"[selfie/aivid] ai_video_broll failed: {e}", exc_info=True)
            clips = []
            gen_err = str(e)[:150]
        # Log spend regardless of whether the flow is still alive — the money is gone.
        _LOGGER.info(f"[selfie/aivid] user={user_id} Seedance ~${cost:.2f} for {len(clips)} clips")

        cur = _PENDING.get(user_id) or {}
        _PICK_STATES = (
            "selfie_broll_offer", "selfie_broll_picking",
            "selfie_broll_uploading_photo", "selfie_broll_uploading_video",
        )
        if not _aivid_job_matches(cur, job_id) or cur.get("state") not in _PICK_STATES:
            # newer run / cancel replaced our key — don't apply our (paid) clips.
            # If the key is still OURS (only the state moved on), release it so the
            # guard doesn't linger ~15 min; if a newer run owns it, leave it alone.
            if _aivid_job_matches(cur, job_id):
                cur.pop("selfie_aivid_job_id", None)
            _drop_gen_dir(cur, gen_dir)
            _SAVE_PENDING(_PENDING)
            await context.bot.send_message(
                chat_id=chat_id,
                text="ℹ️ Сценарий изменился за время генерации — AI-видео не применил.",
            )
            return True

        if not clips:
            cur.pop("selfie_aivid_job_id", None)   # clear so the user can retry (no lockout)
            _drop_gen_dir(cur, gen_dir)
            _SAVE_PENDING(_PENDING)
            items = _items_from_pending(cur)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "❌ Не удалось сгенерировать AI-видео. "
                    + (f"({gen_err}) " if gen_err else "")
                    + "Попробуй ещё раз или выбери B-roll из библиотеки.\n\n"
                    + selfie_broll.build_picker_message(items)
                ),
                reply_markup=selfie_broll.build_picker_keyboard(items),
            )
            return True

        items = _items_from_pending(cur)
        free_now = selfie_broll.MAX_BROLL_ITEMS - len(items)
        added = 0
        for clip in clips[:max(0, free_now)]:
            _store_item(cur, selfie_broll.BrollItem(
                kind="video", source=Path(clip), label=f"[AI-видео] {Path(clip).stem}",
            ))
            added += 1
        cur.pop("selfie_aivid_job_id", None)
        _SAVE_PENDING(_PENDING)
        items = _items_from_pending(cur)
        # Honest about paid-but-unused: clips are already generated & billed.
        if added < len(clips):
            _LOGGER.warning(
                f"[selfie/aivid] user={user_id} discarded {len(clips) - added} PAID clips (free_now={free_now})")
        extra = "" if added >= len(clips) else (
            f" Сгенерировано {len(clips)} (оплачено), но в лимит влезло {added} — "
            "остальные не добавил."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🎬 Готово — добавил {added} AI-видео-клип(а).{extra}\n\n"
                + selfie_broll.build_picker_message(items)
            ),
            reply_markup=selfie_broll.build_picker_keyboard(items),
        )
        return True

    # «Из библиотеки» (фото/клипы) → подменю КАТЕГОРИЙ (только непустые).
    # Источник фото — broll-library/photos/<brand>/<cat> (НЕ обложки), фикс
    # 10 июня. Если одна категория — сразу её сэмплы; если пусто — сообщение.
    if action in ("lib_photo", "lib_clip"):
        kind = "image" if action == "lib_photo" else "video"
        cats = await asyncio.to_thread(selfie_broll.list_library_categories, kind)
        if not cats:
            await query.edit_message_text(
                "⚠️ Библиотека пуста — добавь файлы или загрузи своё."
            )
            return True
        if len(cats) == 1:
            await _show_broll_category_samples(query, context, user_id, kind, cats[0][0])
            return True
        _sel = len(_items_from_pending(data))
        await query.edit_message_text(
            "📷 Фото из библиотеки — выбери категорию:" if kind == "image"
            else "🎞 Клипы из библиотеки — выбери категорию:",
            reply_markup=selfie_broll.build_category_keyboard(kind, cats, _sel),
        )
        return True

    if action == "cat":
        src_tag = parts[2] if len(parts) > 2 else "photo"
        cat = parts[3] if len(parts) > 3 else ""
        kind = "image" if src_tag == "photo" else "video"
        await _show_broll_category_samples(query, context, user_id, kind, cat)
        return True

    if action == "catback":
        src_tag = parts[2] if len(parts) > 2 else "photo"
        kind = "image" if src_tag == "photo" else "video"
        cats = await asyncio.to_thread(selfie_broll.list_library_categories, kind)
        if not cats:
            await _show_broll_picker_screen(query, user_id)
            return True
        total = len(_items_from_pending(data))
        await query.edit_message_text(
            (f"📷 Категория фото (выбрано {total}/{selfie_broll.MAX_BROLL_ITEMS}):"
             if kind == "image"
             else f"🎞 Категория клипов (выбрано {total}/{selfie_broll.MAX_BROLL_ITEMS}):"),
            reply_markup=selfie_broll.build_category_keyboard(kind, cats, total),
        )
        return True

    if action == "reroll":
        src_tag = parts[2] if len(parts) > 2 else "photo"
        cat = parts[3] if len(parts) > 3 else ""
        kind = "image" if src_tag == "photo" else "video"
        shown_key = f"{src_tag}:{cat or '_all'}"
        data.setdefault("selfie_broll_shown_ids", {})
        shown = list(data["selfie_broll_shown_ids"].get(shown_key, []))
        samples = await asyncio.to_thread(
            selfie_broll.list_library_sample, kind, cat, 6, shown,
        )
        if not samples:
            await query.answer("Больше нечего показать в этой категории.", show_alert=True)
            return True
        data["selfie_broll_shown_ids"][shown_key] = shown + [s["id"] for s in samples]
        data["selfie_broll_batch"] = {
            "kind": kind, "category": cat,
            "items": [{"id": s["id"], "path": s["path"]} for s in samples],
        }
        _SAVE_PENDING(_PENDING)
        chat_id = query.message.chat_id
        # Фидбэк сразу: генерация 6 видео-превью (ffmpeg) занимает несколько сек —
        # без этого кажется, что бот завис (Артём 8 июня).
        try:
            await query.edit_message_text("🔄 Готовлю ещё 6 превью…")
        except Exception:
            pass
        await _send_broll_previews(context, chat_id, samples, kind)
        selected_ids = _selected_lib_ids(data)
        total = len(_items_from_pending(data))
        icon = "📷" if kind == "image" else "🎞"
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{icon} Ещё варианты — тапни номер. Выбрано: {total}/{selfie_broll.MAX_BROLL_ITEMS}.",
            reply_markup=selfie_broll.build_toggle_keyboard(
                samples, kind, cat, selected_ids, total),
        )
        return True

    if action == "tog":
        # selfie_broll:tog:<src>:<cat>:<id> (full split — cat/id без ':')
        p = query.data.split(":")
        src_tag = p[2] if len(p) > 2 else "photo"
        cat = p[3] if len(p) > 3 else ""
        item_id = p[4] if len(p) > 4 else ""
        kind = "image" if src_tag == "photo" else "video"
        raw = data.setdefault("selfie_broll_items", [])
        sel_label = f"library/{item_id}"
        existing = next((d for d in raw if d.get("label") == sel_label), None)
        if existing:
            raw.remove(existing)
            removed = True
        else:
            removed = False
            if len(raw) >= selfie_broll.MAX_BROLL_ITEMS:
                await query.answer(
                    f"Лимит {selfie_broll.MAX_BROLL_ITEMS} — сними что-то.", show_alert=True)
                return True
            src_path = await asyncio.to_thread(
                selfie_broll.lookup_library_path, kind, item_id)
            if not src_path or not Path(src_path).exists():
                await query.answer("⚠️ Файл не найден"); return True
            raw.append({"kind": kind, "source": src_path, "label": sel_label})
        _SAVE_PENDING(_PENDING)
        # Перерисовать клавиатуру (✅/счётчик) на этом же сообщении.
        batch = data.get("selfie_broll_batch") or {}
        b_samples = [{"id": b["id"]} for b in batch.get("items", [])]
        selected_ids = _selected_lib_ids(data)
        total = len(raw)
        try:
            await query.edit_message_reply_markup(
                reply_markup=selfie_broll.build_toggle_keyboard(
                    b_samples, batch.get("kind", kind), batch.get("category", cat),
                    selected_ids, total))
        except Exception:
            pass
        await query.answer("снято" if removed else "✅ добавлено")
        return True

    if action == "remove_last":
        items_raw = data.get("selfie_broll_items", [])
        if items_raw:
            items_raw.pop()
            _SAVE_PENDING(_PENDING)
        await _show_broll_picker_screen(query, user_id)
        return True

    if action == "review":
        # «Моё выбранное» — обзор всего набора (фото+клипы) с удалением «🗑 N».
        await _show_broll_review(query, user_id)
        return True

    if action == "rm":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
        items_raw = data.get("selfie_broll_items", []) or []
        if 0 <= idx < len(items_raw):
            items_raw.pop(idx)
            _SAVE_PENDING(_PENDING)
            await query.answer("Убрано")
        await _show_broll_review(query, user_id)
        return True

    if action == "back":
        await _show_broll_picker_screen(query, user_id)
        return True

    if action == "upload_photo":
        data["state"] = "selfie_broll_uploading_photo"
        _SAVE_PENDING(_PENDING)
        await query.edit_message_text(
            "📤 Пришли фотографию (как фото, не как файл).\n\n"
            "Она будет добавлена к B-roll как Ken Burns-вставка (~2.8 сек).\n\n"
            "Или жми «⬅️ Назад» чтобы вернуться к picker'у.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Назад", callback_data="selfie_broll:back"),
            ]]),
        )
        return True

    if action == "upload_video":
        data["state"] = "selfie_broll_uploading_video"
        _SAVE_PENDING(_PENDING)
        await query.edit_message_text(
            "📤 Пришли видео-файл (короткий клип 2-5 сек идеально).\n\n"
            "Он будет вставлен как B-roll-сегмент (звук от видео отключается).\n\n"
            "Или жми «⬅️ Назад» чтобы вернуться к picker'у.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Назад", callback_data="selfie_broll:back"),
            ]]),
        )
        return True

    if action == "done":
        items = _items_from_pending(data)
        if not items:
            await query.edit_message_text("⚠️ Список пуст. Выбери хотя бы один B-roll или жми «Отмена».")
            await _show_broll_picker_screen(query, user_id)
            return True
        # Вместо авто-смарт — показать выбор формата сборки (как в card-пути).
        await query.edit_message_text(
            _montage_format_message(len(items)),
            reply_markup=_montage_format_keyboard(),
        )
        return True

    if action == "asm":
        code = parts[2] if len(parts) > 2 else "m"
        items = _items_from_pending(data)
        if not items:
            await query.edit_message_text("⚠️ Список B-roll пуст. Начни заново через /selfie.")
            return True
        await _run_broll_assembly_and_proceed(
            query, context, user_id, items, layout_code=code)
        return True

    return False


async def handle_broll_upload_photo_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Photo message handler for state selfie_broll_uploading_photo.

    Returns True if the photo was consumed (state advanced), False otherwise.
    Called from bot.py:process_photo BEFORE other guards.
    """
    user_id = update.effective_user.id
    data = _PENDING.get(user_id)
    if not data or data.get("state") != "selfie_broll_uploading_photo":
        return False

    photos = update.message.photo or []
    if not photos:
        await update.message.reply_text("Это не фото. Пришли photo (не файл).")
        return True

    selfie_tmp = Path(data["selfie_tmp_dir"])
    uploads_dir = selfie_tmp / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    n_existing = len([p for p in uploads_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")])
    dest = uploads_dir / f"upload_photo_{n_existing + 1:03d}.jpg"

    largest = photos[-1]  # highest resolution
    tg_file = await context.bot.get_file(largest.file_id)
    await tg_file.download_to_drive(str(dest))

    new_item = selfie_broll.BrollItem(kind="image", source=dest, label=f"upload/{dest.name}")
    items = _items_from_pending(data)
    err = selfie_broll.validate_added(items, new_item)
    if err:
        await update.message.reply_text(f"⚠️ {err}")
    else:
        _store_item(data, new_item)
    data["state"] = "selfie_broll_picking"
    _SAVE_PENDING(_PENDING)

    await _show_broll_picker_screen(update.message, user_id)
    return True


async def handle_broll_upload_video_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Video message handler for state selfie_broll_uploading_video."""
    user_id = update.effective_user.id
    data = _PENDING.get(user_id)
    if not data or data.get("state") != "selfie_broll_uploading_video":
        return False

    video_file = update.message.video or update.message.document
    is_video = (
        update.message.video is not None
        or (video_file is not None and video_file.mime_type and video_file.mime_type.startswith("video/"))
    )
    if not is_video:
        await update.message.reply_text("Это не видео. Пришли видеофайл (MP4).")
        return True

    selfie_tmp = Path(data["selfie_tmp_dir"])
    uploads_dir = selfie_tmp / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    n_existing = len([p for p in uploads_dir.iterdir() if p.suffix.lower() in (".mp4", ".mov", ".m4v")])
    dest = uploads_dir / f"upload_video_{n_existing + 1:03d}.mp4"

    tg_file = await context.bot.get_file(video_file.file_id)
    await tg_file.download_to_drive(str(dest))

    new_item = selfie_broll.BrollItem(kind="video", source=dest, label=f"upload/{dest.name}")
    items = _items_from_pending(data)
    err = selfie_broll.validate_added(items, new_item)
    if err:
        await update.message.reply_text(f"⚠️ {err}")
    else:
        _store_item(data, new_item)
    data["state"] = "selfie_broll_picking"
    _SAVE_PENDING(_PENDING)

    await _show_broll_picker_screen(update.message, user_id)
    return True


# Форматы сборки — тот же набор, что и в card-пути (card_asm_go). Код кнопки →
# (название, описание). Селфи уже с прожжёнными субтитрами, поэтому без
# «+ субтитры» toggle.
_MONTAGE_FORMATS = {
    "m": ("🎯 Смарт-микс", "видео целиком на весь экран + фото в сплит"),
    "s": ("🔲 Сплит", "B-roll сверху + аватар снизу (50/50)"),
    "d": ("🎥 Динамический", "аватар ↔ B-roll на весь экран"),
    "p": ("🎬 Про-монтаж", "хук-аватар → 50/50 → CTA (фикс. формат)"),
    "a": ("🧠 ИИ-монтаж", "Claude читает сценарий и B-roll, сам решает раскладку"),
    "f": ("📺 Full-screen", "хук-аватар → ВЕСЬ B-roll на полный экран → аватар-CTA (без сплитов)"),
}
# Порядок кнопок в меню.
_MONTAGE_ORDER = ("m", "s", "d", "p", "a", "f")
# Код кнопки → layout для assemble_auto_montage. 'a' (ИИ) тоже идёт через 'pro',
# но с планом от Claude (generate_montage_plan), а не детерминированным bookend.
_SELFIE_LAYOUT_MAP = {"s": "split", "d": "dynamic", "p": "pro", "a": "pro",
                      "m": "smart", "f": "fullscreen"}


def _montage_format_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_MONTAGE_FORMATS[c][0], callback_data=f"selfie_broll:asm:{c}")]
        for c in _MONTAGE_ORDER
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад к B-roll", callback_data="selfie_broll:back")])
    return InlineKeyboardMarkup(rows)


def _montage_format_message(n_items: int) -> str:
    lines = [f"🎬 Выбери формат сборки (B-roll: {n_items}):\n"]
    for c in _MONTAGE_ORDER:
        name, desc = _MONTAGE_FORMATS[c]
        lines.append(f"{name} — {desc}")
    lines.append(
        "\n💡 Под видео+фото вместе: Смарт-микс (предсказуемо) или ИИ-монтаж "
        "(умнее, но +вызов Claude)."
    )
    return "\n".join(lines)


async def _run_broll_assembly_and_proceed(
    query, context, user_id: int, items: list, layout_code: str = "m",
) -> None:
    """Assemble selfie + B-roll via existing video_assembler, advance to music.

    layout_code: m=smart, s=split, d=dynamic, p=pro(bookend), a=pro(ИИ-план).
    Логика сборки целиком переиспользует assemble_auto_montage / build_bookend_
    montage_plan / generate_montage_plan (тот же движок, что и card-путь)."""
    data = _PENDING[user_id]
    selfie_tmp = Path(data["selfie_tmp_dir"])
    subtitled = Path(data["selfie_subtitled"])
    fmt_name = _MONTAGE_FORMATS.get(layout_code, _MONTAGE_FORMATS["m"])[0]
    layout = _SELFIE_LAYOUT_MAP.get(layout_code, "smart")
    is_ai = layout_code == "a"

    await query.edit_message_text(
        f"{fmt_name}: собираю монтаж — селфи + {len(items)} B-roll… ~30-90 сек."
    )

    try:
        # 1. Подложить ЧИСТОЕ селфи (без субтитров) как «аватар». Субтитры
        # наложим на ФИНАЛЬНЫЙ монтаж с адаптивной позицией (split→стык 50/50,
        # fullscreen→низ) — иначе они пропадали в broll_full и ужимались в split
        # (раньше брали subtitled.mp4, субтитры ехали вместе с аватаром).
        clean_selfie = Path(data.get("selfie_source") or subtitled)
        project_dir = selfie_tmp / "assembly"
        project_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(selfie_broll.place_selfie_as_avatar, clean_selfie, project_dir)
        await asyncio.to_thread(selfie_broll.prepare_broll_in_project, items, project_dir)
        # Клипы скопированы в project_dir → tmp AI-генерации больше не нужен.
        _cleanup_selfie_gen_dirs(data)

        from video_assembler import (
            assemble_auto_montage, build_bookend_montage_plan, _probe_duration,
        )

        # 2. Для Про-монтажа / ИИ-монтажа (layout="pro") нужен montage_plan.
        montage_plan = None
        if layout == "pro":
            avatar_dur = await asyncio.to_thread(_probe_duration, clean_selfie)
            n_broll = len(items)
            if is_ai:
                await query.edit_message_text(
                    f"{fmt_name}: Claude строит план по сценарию… ~10-20 сек."
                )
                from bot import generate_montage_plan
                script_text = data.get("selfie_transcript", "") or ""
                # Реальные описания выбранных клипов из .json-сайдкаров библиотеки
                # (а не «B-roll #1») — Claude кладёт клип под фразу осмысленно.
                descs = selfie_broll.build_broll_descriptions(items)
                _has_photo = any(getattr(it, "kind", None) == "image" for it in items)
                montage_plan = await asyncio.to_thread(
                    generate_montage_plan, script_text, descs, avatar_dur, _has_photo,
                )
            else:
                montage_plan = await asyncio.to_thread(
                    build_bookend_montage_plan, avatar_dur, n_broll,
                )
            await query.edit_message_text(
                f"{fmt_name}: собираю видео по плану… ~1-3 мин."
            )

        # 3. Запустить assembler выбранным форматом + субтитры НА ФИНАЛЬНОМ
        # монтаже с готовыми (отредактированными) словами — адаптивная позиция
        # по лейауту сегмента, без ре-транскрибации.
        final_auto = await asyncio.to_thread(
            assemble_auto_montage,
            project_dir,
            layout=layout,
            montage_plan=montage_plan,
            subtitles=True,
            subtitle_words=(data.get("selfie_words") or None),
            broll_mode="real",
            brand_name="maksim",
        )
        _LOGGER.info(
            f"[selfie] B-roll assembly ({layout_code}/{layout}) done: "
            f"{final_auto.stat().st_size / 1024 / 1024:.1f} MB"
        )

        # 4. Подменить selfie_subtitled на смонтированное видео — теперь music
        # mixer добавит музыку на final_auto.mp4 а не на чистый selfie.
        data["selfie_subtitled"] = str(final_auto)
        data["selfie_final"] = str(final_auto)
        data["selfie_montage_format"] = layout_code
        data["state"] = "selfie_music_picking"
        _SAVE_PENDING(_PENDING)

        await query.edit_message_text(
            f"✅ Монтаж готов ({fmt_name}, {len(items)} B-roll).\n\n"
            f"{selfie_music.build_music_picker_message()}",
            reply_markup=selfie_music.category_keyboard(),
            parse_mode="HTML",
        )
    except Exception as e:
        _LOGGER.error(
            f"[selfie] B-roll assembly failed ({layout_code}): {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Не получилось собрать монтаж: {e}\n\n"
            "Возвращаю к выбору B-roll. Можно убрать что-то и попробовать снова, "
            "либо нажать «Отмена» чтобы пропустить B-roll.",
        )
        data["state"] = "selfie_broll_picking"
        _SAVE_PENDING(_PENDING)
        await _show_broll_picker_screen(query, user_id)


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
    if not data or data.get("state") not in (
        "selfie_cover_picking", "selfie_cover_uploading", "selfie_cover_confirming",
    ):
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

    if action == "confirm":
        # Снимаем кнопки со старого фото-превью, чтобы повторный клик не дал
        # ложное «сессия не найдена» (state уже уйдёт в waiting_title).
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        # Юзер подтвердил превью обложки → финализируем сохранённый путь.
        cover_path = data.get("selfie_cover_pending_path")
        note = data.get("selfie_cover_pending_note", "обложка")
        if not cover_path or not Path(cover_path).exists():
            await query.message.reply_text(
                "⚠️ Превью обложки потерялось. Выбери заново:",
                reply_markup=selfie_cover.cover_picker_keyboard(),
            )
            data["state"] = "selfie_cover_picking"
            _SAVE_PENDING(_PENDING)
            return True
        await _finalize_with_cover(
            message_or_query=query.message,
            context=context,
            user_id=user_id,
            cover_path=Path(cover_path),
            cover_note=note,
        )
        return True

    if action == "reject":
        # Снимаем кнопки со старого превью перед возвратом к picker'у.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        # «Выбрать другую» → назад к picker'у обложки.
        data["state"] = "selfie_cover_picking"
        data.pop("selfie_cover_pending_path", None)
        data.pop("selfie_cover_pending_note", None)
        _SAVE_PENDING(_PENDING)
        await query.message.reply_text(
            selfie_cover.build_picker_message(),
            reply_markup=selfie_cover.cover_picker_keyboard(),
            parse_mode="HTML",
        )
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
        await _show_library(query, context, user_id, reroll=False)
        return True

    if action == "lib_reroll":
        await _show_library(query, context, user_id, reroll=True)
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
        # Без confirm — юзер сам прислал это фото и уже его видел.
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
    # Не коммитим молча — показываем извлечённый кадр и просим подтвердить.
    await _show_cover_confirm(query, context, user_id, cover_path, note=note)


async def _show_cover_confirm(
    query_or_msg, context, user_id: int, cover_path: Path, note: str,
) -> None:
    """Показать фото-превью обложки + кнопки [✅ Да / 🔄 Другую].

    Единый confirm-шаг для ВСЕХ источников обложки (кадр / библиотека /
    upload). Не коммитим обложку молча — юзер сначала видит фото и
    подтверждает (правило preview/confirm, 9 июня).
    """
    data = _PENDING[user_id]
    data["state"] = "selfie_cover_confirming"
    data["selfie_cover_pending_path"] = str(cover_path)
    data["selfie_cover_pending_note"] = note
    _SAVE_PENDING(_PENDING)

    chat_id = (
        query_or_msg.message.chat_id
        if hasattr(query_or_msg, "message") and query_or_msg.message
        else query_or_msg.chat_id
    )
    try:
        with open(cover_path, "rb") as ph:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=ph,
                caption=f"🖼 Вот обложка ({note}). Подходит?",
                reply_markup=selfie_cover.confirm_keyboard(),
            )
    except Exception as e:
        _LOGGER.error(f"[selfie] send_photo confirm failed: {e}")
        # Fallback — текстовое подтверждение, чтобы флоу не залип.
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🖼 Обложка готова ({note}). Подходит?",
            reply_markup=selfie_cover.confirm_keyboard(),
        )


async def _show_library(query, context, user_id: int, reroll: bool) -> None:
    """Показать 6 случайных фото из библиотеки КАК ФОТО (send_photo).

    Каждое фото — отдельное сообщение с кнопкой «✅ Выбрать эту» под ним,
    чтобы юзер видел что выбирает. В конце footer с «Ещё 6 / Назад».
    (Раньше слался текстовый список ID — юзер не видел фотографий, баг 9 июня.)
    """
    data = _PENDING[user_id]
    shown = data.get("selfie_cover_shown_lib_ids", [])
    exclude = shown if reroll else None
    sample = await asyncio.to_thread(
        selfie_cover.list_library_sample, 6, exclude
    )

    chat_id = query.message.chat_id

    if not sample:
        await query.edit_message_text(
            selfie_cover.build_library_message([]),  # «библиотека пуста»
            reply_markup=selfie_cover.cover_picker_keyboard(),
            parse_mode="HTML",
        )
        return

    # Запоминаем показанные id для reroll
    if reroll:
        data["selfie_cover_shown_lib_ids"] = list(shown) + [s["id"] for s in sample]
    else:
        data["selfie_cover_shown_lib_ids"] = [s["id"] for s in sample]
    _SAVE_PENDING(_PENDING)

    # Обновляем исходное сообщение коротким статусом (убираем старые кнопки).
    suffix = " (ещё 6)" if reroll else ""
    try:
        await query.edit_message_text(f"📚 Загружаю фото из библиотеки{suffix}...")
    except Exception as e:
        _LOGGER.debug(f"[selfie] library intro edit skipped: {e}")

    # 1) Каждое фото — отдельным сообщением с кнопкой «✅ Выбрать эту».
    for item in sample:
        try:
            with open(item["path"], "rb") as ph:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=ph,
                    caption=item["id"],
                    reply_markup=selfie_cover.library_pick_keyboard(item["id"]),
                )
        except Exception as e:
            _LOGGER.error(f"[selfie] library send_photo failed for {item['id']}: {e}")

    # 2) Footer с «Ещё 6 / Назад» — остаётся последним в ленте.
    await context.bot.send_message(
        chat_id=chat_id,
        text="☝️ Выбери фото кнопкой «✅ Выбрать эту» под нужным.\n"
             "Или «🔄 Ещё 6» / «⬅️ Назад».",
        reply_markup=selfie_cover.library_footer_keyboard(),
    )


async def _pick_library_cover(query, context, user_id: int, photo_id: str) -> None:
    """Юзер выбрал фото из библиотеки → копируем в tmp и сразу финализируем.

    БЕЗ confirm-шага: фото было видно в сетке превью, клик «✅ Выбрать эту»
    = осознанный выбор (Артём 9 июня: повторный «Подходит?» избыточен).
    Confirm остаётся только для кадра (там слепой выбор позиции)."""
    import shutil as _shutil
    data = _PENDING[user_id]
    selfie_tmp = Path(data["selfie_tmp_dir"])

    src = selfie_cover.lookup_library_path(photo_id)
    if not src or not Path(src).exists():
        await query.message.reply_text(
            f"⚠️ Не нашёл фото {photo_id}. Выбери другое.",
            reply_markup=selfie_cover.cover_picker_keyboard(),
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
        await query.message.reply_text(
            f"⚠️ Ошибка копирования: {e}",
            reply_markup=selfie_cover.cover_picker_keyboard(),
        )
        return

    # Снимаем кнопку «Выбрать эту» с выбранного фото, чтобы повторный клик
    # не дал ложное «сессия не найдена» после ухода в waiting_title.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _finalize_with_cover(
        message_or_query=query.message,
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

    # Шаг «текст на обложку?» (С текстом/Без) — если хост предоставил. Он сам
    # потом вызовет title_picker. Иначе — сразу к title_picker.
    if _COVER_TEXT_STEP is not None:
        await _COVER_TEXT_STEP(
            message_or_query, context, user_id, str(cover_path), transcript_text
        )
        return

    # If the host bot provided a richer title-picker (e.g. Claude-generated
    # hooks), delegate to it. Otherwise show the built-in single-button UI.
    if _TITLE_PICKER is not None:
        await _TITLE_PICKER(
            message_or_query, context, user_id, transcript_text, first_sentence
        )
        return

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
