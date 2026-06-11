"""selfie.music — выбор и микширование фоновой музыки для selfie-pipeline.

Тонкая обёртка над music_mixer.py с UI-helpers (категории как клавиатура,
формат сообщений). Реальный mix делает music_mixer.mix_music_into_video.

Категории читаются из /root/content-bot/music/tracks.json (5 шт):
  chill 😌 / energetic 🔥 / corporate 💼 / cinematic 🎬 / inspiring ✨

Callbacks (data-prefix "selfie_music:"):
  - "selfie_music:cat:<category>"     → юзер выбрал категорию
  - "selfie_music:reroll:<category>"  → дай другой случайный трек той же категории
  - "selfie_music:back"               → вернуться к выбору категории
  - "selfie_music:skip"               → пропустить музыку (использовать subtitled.mp4)
  - "selfie_music:accept"             → ОК, идём к запросу названия
"""
from __future__ import annotations

import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Lazy/optional import — на dev-машине без серверного music_mixer setup
# модуль всё равно должен импортироваться (для unit-тестов с моками).
try:
    from music_mixer import (
        list_categories as _list_cats,
        list_tracks as _list_tracks,
        mix_music_into_video as _mix_into_video,
    )
except ImportError:
    def _list_cats() -> dict:
        return {}

    def _list_tracks(category: str) -> list:
        return []

    def _mix_into_video(*args, **kwargs) -> bool:
        return False


# Какие категории показываем юзеру и в каком порядке
CATEGORY_ORDER = ["chill", "energetic", "corporate", "cinematic", "inspiring"]


def get_visible_categories() -> list[dict]:
    """Список категорий для UI в фиксированном порядке.

    Returns:
        [{"cat": str, "label": str, "emoji": str, "desc": str}, ...]
        Скипает категории, которых нет в tracks.json.
    """
    meta_by_cat = _list_cats()
    out = []
    for cat in CATEGORY_ORDER:
        if cat in meta_by_cat:
            m = meta_by_cat[cat]
            out.append({
                "cat": cat,
                "label": m.get("label", cat),
                "emoji": m.get("emoji", ""),
                "desc": m.get("desc", ""),
            })
    return out


def pick_random_track(category: str, exclude_id: str | None = None) -> dict | None:
    """Случайный трек из категории.

    Args:
        category: имя категории (chill/energetic/...).
        exclude_id: id трека, который НЕ возвращать (для "другой трек"). Если
            это единственный доступный — fallback: возвращаем его же.

    Returns:
        Track dict {"id", "file", "duration", "size_mb"} либо None если категория пуста.
    """
    tracks = _list_tracks(category)
    if not tracks:
        return None
    if exclude_id is not None:
        filtered = [t for t in tracks if t.get("id") != exclude_id]
        if filtered:
            return random.choice(filtered)
        # Все треки совпали с exclude — fallback, лучше что-то, чем None
    return random.choice(tracks)


def mix_into_video(video_path: str, music_path: str, output_path: str) -> bool:
    """Тонкая обёртка для тестируемости (mockable).

    Returns:
        True если mix успешен и output_path создан.
    """
    return _mix_into_video(video_path, music_path, output_path)


# ── Клавиатуры ──────────────────────────────────────────────────────────────


def category_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора категории музыки.

    Раскладка: по 2 кнопки в ряду + последняя одна (если категорий нечётное число)
    + «Без музыки» + «Отмена».
    """
    cats = get_visible_categories()
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(cats), 2):
        row = []
        for c in cats[i:i + 2]:
            label = f"{c['emoji']} {c['label']}".strip()
            row.append(InlineKeyboardButton(
                label,
                callback_data=f"selfie_music:cat:{c['cat']}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("🚫 Без музыки", callback_data="selfie_music:skip")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def picked_keyboard(category: str) -> InlineKeyboardMarkup:
    """Клавиатура ПОСЛЕ выбора трека и микса.

    4 действия:
      ✅ accept   — переходим к названию
      🔄 reroll   — другой случайный трек той же категории
      ⬅️ back     — вернуться к выбору категории
      🚫 skip     — отказаться от музыки
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, дальше", callback_data="selfie_music:accept")],
        [InlineKeyboardButton("🔄 Другой трек", callback_data=f"selfie_music:reroll:{category}")],
        [InlineKeyboardButton("⬅️ Сменить категорию", callback_data="selfie_music:back")],
        [InlineKeyboardButton("🚫 Без музыки", callback_data="selfie_music:skip")],
    ])


# ── Тексты сообщений ────────────────────────────────────────────────────────


def build_music_picker_message() -> str:
    """Сообщение со списком категорий для выбора."""
    return (
        "🎵 <b>Выбери настроение музыки</b>\n\n"
        "Музыка ляжет фоном под голос. Голос приоритетней — "
        "при речи музыка автоматически притихнет (sidechain ducking).\n\n"
        "Можно пропустить — отдашь видео только с голосом и субтитрами."
    )


def build_picked_message(
    category_label: str,
    track: dict,
    video_size_mb: float | None = None,
) -> str:
    """Сообщение после успешного микса трека в видео.

    Args:
        category_label: человеческий заголовок категории ("Спокойный / Фон").
        track: dict с id/duration/file/size_mb.
        video_size_mb: размер итогового видео в МБ (опционально).
    """
    name = track.get("id", "?")
    dur = track.get("duration", 0)
    size_line = f"\n📦 Видео: {video_size_mb:.1f} MB" if video_size_mb else ""
    return (
        f"🎵 Подобрал трек из категории <b>{category_label}</b>:\n"
        f"<code>{name}</code> · {dur:.0f}s{size_line}\n\n"
        f"Микс готов. Устраивает или попробовать другой?"
    )
