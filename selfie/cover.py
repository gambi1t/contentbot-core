"""selfie.cover — выбор обложки для selfie-pipeline.

Три источника (по итогам обсуждения 4 июня 2026):
  - frame: один из 3 кадров видео (start / mid / end)
  - upload: юзер шлёт своё фото
  - library: 6 случайных фото из paths.LIBRARY_PHOTOS_DIR
             (по умолчанию <bot_root>/broll-library/photos)
             с пагинацией «🔄 Ещё 6»

Callbacks (data-prefix "selfie_cover:"):
  - "selfie_cover:frame:start|mid|end"   → ffmpeg snapshot указанного timestamp
  - "selfie_cover:upload"                → state selfie_cover_uploading
  - "selfie_cover:library"               → показать 6 случайных
  - "selfie_cover:lib_pick:<id>"         → выбрать конкретное фото
  - "selfie_cover:lib_reroll"            → следующие 6 (exclude уже показанные)
  - "selfie_cover:back"                  → вернуться к picker
  - "selfie_cover:skip"                  → дефолт = первый кадр
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import paths

# Cover library root — through paths.COVER_LIBRARY_DIR (env-overridable).
# Это ГОТОВЫЕ ПОРТРЕТЫ/АВАТАРЫ Максима (assets/avatars/maksim), а НЕ B-roll
# footage (glamping/karting). «Из библиотеки» при выборе обложки = эти фото.
# На dev-машине если папки нет — модуль импортируется нормально, но
# list_library_sample вернёт пустой список.
_LIBRARY_DIR = paths.COVER_LIBRARY_DIR

_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


# ── Frame timestamps ────────────────────────────────────────────────────────


def get_frame_timestamps(duration_seconds: float) -> list[float]:
    """Вернуть 3 timestamp'а для snapshot: start (1 сек), середина, конец (-1 сек).

    Для очень коротких видео (< 3 сек) все три кадра прижимаются внутрь диапазона
    и могут быть близкими — это допустимо, главное чтобы не было out-of-range.
    """
    if duration_seconds <= 0:
        return [0.0, 0.0, 0.0]
    # Для нормальных видео (>= 2 сек) start = ровно 1 сек.
    # Для очень коротких — пропорционально, чтобы не вылезти.
    start = 1.0 if duration_seconds >= 2.0 else duration_seconds * 0.1
    mid = duration_seconds / 2.0
    end = max(0.0, duration_seconds - 1.0)
    # Защита от out-of-range
    cap = max(0.0, duration_seconds - 0.1)
    return [round(min(t, cap), 2) for t in (start, mid, end)]


def extract_frame(video_path: str | Path, timestamp_seconds: float, output_path: str | Path) -> bool:
    """Извлечь один кадр из видео в указанный момент через ffmpeg.

    Args:
        video_path: исходное видео
        timestamp_seconds: момент в секундах
        output_path: куда сохранить JPG

    Returns:
        True если файл создан и не пуст.
    """
    try:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # -ss перед -i = быстрый seek (key-frame approx) — для cover хватит
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(max(0.0, timestamp_seconds)),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return False
        return out.exists() and out.stat().st_size > 1000
    except Exception:
        return False


def probe_video_duration(video_path: str | Path) -> float:
    """ffprobe → длительность в секундах. 0 если не удалось."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


# ── Library ─────────────────────────────────────────────────────────────────


def _scan_library() -> list[dict]:
    """Просканировать _LIBRARY_DIR рекурсивно и вернуть [{id, path}, ...].

    id = basename без расширения (стабильный, переживает рестарты).
    """
    if not _LIBRARY_DIR or not _LIBRARY_DIR.exists():
        return []
    out = []
    for p in _LIBRARY_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in _IMG_EXT:
            out.append({"id": p.stem, "path": str(p)})
    return out


def list_library_sample(n: int = 6, exclude_ids: list[str] | None = None) -> list[dict]:
    """Случайные n фото из библиотеки.

    Args:
        n: сколько хочется (если в библиотеке меньше — вернёт все доступные).
        exclude_ids: id, которых не хотим (для reroll). Если после фильтра меньше n —
            вернётся столько, сколько осталось (без дублирования exclude).

    Returns:
        [{"id": str, "path": str}, ...] — пустой список если библиотеки нет.
    """
    all_photos = _scan_library()
    if not all_photos:
        return []
    if exclude_ids:
        exclude_set = set(exclude_ids)
        all_photos = [p for p in all_photos if p["id"] not in exclude_set]
    if not all_photos:
        return []
    return random.sample(all_photos, min(n, len(all_photos)))


def lookup_library_path(photo_id: str) -> str | None:
    """Найти путь фото по id (для callback после клика на превью).

    Возвращает None если id не найден.
    """
    for p in _scan_library():
        if p["id"] == photo_id:
            return p["path"]
    return None


# ── Keyboards ───────────────────────────────────────────────────────────────


def cover_picker_keyboard() -> InlineKeyboardMarkup:
    """Главный picker обложки: 3 кадра + upload + library + skip + cancel."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📹 Начало", callback_data="selfie_cover:frame:start"),
            InlineKeyboardButton("📹 Середина", callback_data="selfie_cover:frame:mid"),
            InlineKeyboardButton("📹 Финал", callback_data="selfie_cover:frame:end"),
        ],
        [InlineKeyboardButton("📤 Загрузить фото", callback_data="selfie_cover:upload")],
        [InlineKeyboardButton("📚 Из библиотеки", callback_data="selfie_cover:library")],
        [InlineKeyboardButton("➡️ Пропустить (первый кадр)", callback_data="selfie_cover:skip")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])


def library_keyboard(sample: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура выбора из библиотеки.

    Args:
        sample: то, что вернул list_library_sample.

    Раскладка: по 2 кнопки в ряду + reroll + back.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if sample:
        for i in range(0, len(sample), 2):
            row = []
            for idx, item in enumerate(sample[i:i + 2], start=i):
                row.append(InlineKeyboardButton(
                    f"#{idx + 1}",
                    callback_data=f"selfie_cover:lib_pick:{item['id']}",
                ))
            rows.append(row)
        rows.append([InlineKeyboardButton("🔄 Ещё 6", callback_data="selfie_cover:lib_reroll")])
    else:
        # Нет фото — только back
        pass
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="selfie_cover:back")])
    return InlineKeyboardMarkup(rows)


# ── Messages ────────────────────────────────────────────────────────────────


def build_picker_message() -> str:
    return (
        "🖼 <b>Выбери обложку</b>\n\n"
        "Три варианта:\n"
        "• <b>📹 Кадр из видео</b> — начало / середина / финал\n"
        "• <b>📤 Загрузить фото</b> — пришли своё фото отдельным сообщением\n"
        "• <b>📚 Из библиотеки</b> — готовые AI-фото\n\n"
        "Или нажми «➡️ Пропустить» — возьму первый кадр как раньше."
    )


def build_library_message(sample: list[dict]) -> str:
    if not sample:
        return (
            "📚 <b>Библиотека пуста</b>\n\n"
            "В <code>/broll-library/photos</code> нет фото. Используй "
            "«Загрузить фото» или «Кадр из видео»."
        )
    lines = [
        "📚 <b>Библиотека — 6 случайных вариантов</b>",
        "",
        "Нажми «#N» чтобы выбрать. Или «🔄 Ещё 6» для других вариантов.",
        "",
    ]
    for idx, item in enumerate(sample, start=1):
        lines.append(f"  #{idx}: <code>{item['id']}</code>")
    return "\n".join(lines)


def build_upload_prompt_message() -> str:
    return (
        "📤 <b>Пришли фото которое станет обложкой</b>\n\n"
        "Любой формат (JPG/PNG). Один файл — заменю автоматически.\n\n"
        "Или жми «⬅️ Назад» чтобы выбрать другой источник."
    )


def upload_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для state uploading — только кнопка возврата."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="selfie_cover:back")],
    ])


# ── Confirm step (9 июня UX-фикс — не коммитим обложку без подтверждения) ─────


def confirm_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения выбранной обложки (показывается под фото-превью)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, эта обложка", callback_data="selfie_cover:confirm")],
        [InlineKeyboardButton("🔄 Выбрать другую", callback_data="selfie_cover:reject")],
    ])


def library_pick_keyboard(photo_id: str) -> InlineKeyboardMarkup:
    """Кнопка «✅ Выбрать эту» под конкретным фото библиотеки (send_photo превью)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Выбрать эту",
            callback_data=f"selfie_cover:lib_pick:{photo_id}",
        ),
    ]])


def library_footer_keyboard() -> InlineKeyboardMarkup:
    """Footer-клавиатура под пачкой фото-превью библиотеки: ещё / назад."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё 6", callback_data="selfie_cover:lib_reroll")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="selfie_cover:back")],
    ])
