"""selfie.broll_picker — pure helpers for Pipeline 2 (selfie + B-roll).

После шага редактирования субтитров пользователь может добавить B-roll
вставки (фото и/или видео) поверх селфи. Этот модуль готовит данные
для существующего video_assembler.assemble_auto_montage(layout='smart').

Архитектурное решение (8 июня 2026, по указанию Артёма):
  - НЕ строим новый монтажный движок. Переиспользуем assemble_auto_montage,
    который уже умеет ровно то, что нужно (видео — broll_full на полную
    длину, фото — split 2.8с, звук от «аватара»).
  - Селфи с прожжёнными субтитрами кладём в project_dir как avatar_selfie.mp4
    — _find_avatar() забирает его как «лицо».
  - Видео B-roll → broll_NNN.mp4 (нумерация с 001), фото → photos/photo_NNN.<ext>.
"""
from __future__ import annotations

import hashlib
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import paths

MAX_BROLL_ITEMS = 7  # лимит на ролик 30–50 сек (5–7 вставок по ~3 сек).


@dataclass
class BrollItem:
    """Item для B-roll picker.

    kind: "video" (mp4/mov из библиотеки или upload) или "image" (jpg/png/webp).
    source: путь к исходному файлу — будет скопирован в project_dir при сборке.
    label: опциональная метка (например ID файла в библиотеке) для отображения.
    """

    kind: Literal["video", "image"]
    source: Path
    label: str | None = field(default=None)

    def __post_init__(self) -> None:
        if self.kind not in ("video", "image"):
            raise ValueError(
                f"BrollItem.kind must be 'video' or 'image', got {self.kind!r}"
            )
        # Path — strict-cast чтобы str-входы тоже работали.
        self.source = Path(self.source)


def validate_added(current: list[BrollItem], new: BrollItem) -> str | None:
    """Проверить, можно ли добавить ещё один item к текущему списку.

    Returns:
        None — добавлять можно.
        str с сообщением для пользователя — нельзя (лимит превышен).
    """
    if len(current) >= MAX_BROLL_ITEMS:
        return (
            f"Достигнут лимит {MAX_BROLL_ITEMS} B-roll-вставок на ролик. "
            f"Сначала убери что-то из списка."
        )
    return None


def prepare_broll_in_project(items: list[BrollItem], project_dir: Path) -> None:
    """Скопировать item'ы в project_dir с нужными именами для assemble_auto_montage.

    Видео → ``project_dir/broll_NNN.mp4`` (1-based, zero-padded 3).
    Фото → ``project_dir/photos/photo_NNN.<ext>`` (sub-dir создаётся при необходимости).

    Нумерация раздельная: видео-индекс инкрементируется только на видео,
    фото-индекс — только на фото, в порядке вхождения в items.

    Пустой список — no-op.
    """
    project_dir = Path(project_dir)

    video_idx = 0
    photo_idx = 0

    for item in items:
        if item.kind == "video":
            video_idx += 1
            dest = project_dir / f"broll_{video_idx:03d}.mp4"
            shutil.copy2(item.source, dest)
        else:  # image
            photo_idx += 1
            photos_dir = project_dir / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            ext = item.source.suffix.lower() or ".jpg"
            dest = photos_dir / f"photo_{photo_idx:03d}{ext}"
            shutil.copy2(item.source, dest)


def place_selfie_as_avatar(subtitled_path: Path, project_dir: Path) -> Path:
    """Скопировать subtitled.mp4 → project_dir/avatar_selfie.mp4.

    Это даёт video_assembler._find_avatar() правильный файл (он ищет
    ``avatar_*.mp4`` в project_dir). assemble_auto_montage возьмёт это
    видео как «лицо/звук», а сверху наложит B-roll'ы из broll_*.mp4 и
    photos/*.
    """
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    dest = project_dir / "avatar_selfie.mp4"
    shutil.copy2(subtitled_path, dest)
    return dest


# ═══════════════════════════════════════════════════════════════════════════════
#  UI keyboards
# ═══════════════════════════════════════════════════════════════════════════════

def build_offer_keyboard() -> InlineKeyboardMarkup:
    """«🎬 Добавить B-roll?» — Yes/No клавиатура."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, добавить B-roll", callback_data="selfie_broll:add")],
        [InlineKeyboardButton("➡️ Без B-roll, продолжить", callback_data="selfie_broll:skip")],
    ])


def build_picker_keyboard(items: list[BrollItem]) -> InlineKeyboardMarkup:
    """Главный picker: 4 источника + (если есть выбранные) Готово / Убрать + Отмена.

    При достижении ``MAX_BROLL_ITEMS`` кнопки добавления скрываются — юзеру
    предлагают либо убрать что-то, либо завершить.
    """
    rows: list[list[InlineKeyboardButton]] = []
    at_limit = len(items) >= MAX_BROLL_ITEMS

    if not at_limit:
        rows.append([InlineKeyboardButton("📷 Фото из библиотеки", callback_data="selfie_broll:lib_photo")])
        rows.append([InlineKeyboardButton("🎞 Клипы из библиотеки", callback_data="selfie_broll:lib_clip")])
        rows.append([InlineKeyboardButton("📤 Загрузить своё фото", callback_data="selfie_broll:upload_photo")])
        rows.append([InlineKeyboardButton("📤 Загрузить своё видео", callback_data="selfie_broll:upload_video")])
        rows.append([InlineKeyboardButton("🎨 Сгенерировать графику (AI)", callback_data="selfie_broll:gen")])

    if items:
        rows.append([InlineKeyboardButton("🗑 Убрать последний", callback_data="selfie_broll:remove_last")])
        rows.append([
            InlineKeyboardButton(
                f"✅ Готово ({len(items)} выбрано)",
                callback_data="selfie_broll:done",
            ),
        ])

    rows.append([InlineKeyboardButton("❌ Отмена (без B-roll)", callback_data="selfie_broll:cancel")])
    return InlineKeyboardMarkup(rows)


def build_picker_message(items: list[BrollItem]) -> str:
    """Текст-сообщение для picker'а: счётчик + перечисление выбранного.

    Plain text без markdown/HTML — label'ы из библиотеки могут содержать
    underscore/asterisks (e.g. ``c82a51__IMG_7662``), которые ломают разбор
    entities в Telegram.
    """
    n = len(items)
    if n == 0:
        return (
            f"🎬 B-roll: пока ничего не выбрано (макс {MAX_BROLL_ITEMS}).\n\n"
            "Выбери источник:"
        )
    photo_count = sum(1 for it in items if it.kind == "image")
    video_count = sum(1 for it in items if it.kind == "video")

    bullets = []
    for i, it in enumerate(items, 1):
        kind_label = "фото" if it.kind == "image" else "видео"
        label = it.label or it.source.name
        bullets.append(f"  {i}. {kind_label} — {label}")
    bullets_text = "\n".join(bullets)

    return (
        f"🎬 B-roll: {n}/{MAX_BROLL_ITEMS} (фото: {photo_count}, видео: {video_count})\n"
        f"{bullets_text}\n\n"
        "Добавь ещё или жми «Готово»."
    )


_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_VID_EXTS = (".mp4", ".mov", ".m4v")

# Источники B-roll по виду. ФОТО берём из broll-library/photos/<brand>/<cat>
# (категоризированный архив: глэмпинг/картинг/личные/...), а НЕ из обложечной
# COVER_LIBRARY_DIR (портреты Максима) — портрет поверх селфи бессмыслен
# (фикс 10 июня). Клипы — broll-library/clips/<brand>/<cat>.
_LIBRARY_ROOTS = {"image": paths.LIBRARY_PHOTOS_DIR, "video": paths.LIBRARY_CLIPS_DIR}
_EXTS_BY_KIND = {"image": _IMG_EXTS, "video": _VID_EXTS}

# Человекочитаемые подписи категорий (имя папки → emoji + RU).
_CAT_LABELS = {
    "glamping": "🏕 Глэмпинг", "karting": "🏎 Картинг", "sup": "🏄 Сап",
    "personal": "👤 Личные", "maksim_self": "👤 Максим", "team": "👥 Команда",
    "meetings": "🤝 Встречи", "nature": "🌅 Природа", "family": "👨‍👩‍👧 Семья",
    "general": "📦 Общее",
}


def _cat_label(cat: str) -> str:
    return _CAT_LABELS.get(cat, cat.capitalize())


def _kind_root(kind: str) -> Path | None:
    return _LIBRARY_ROOTS.get(kind)


def _brand_base(kind: str) -> Path | None:
    """Папка, под которой лежат категории: ``<root>/<brand>/<cat>``.

    В maksim-bot бренд-папка = ``maksim``. Если её нет — корень (плоская
    структура), тогда категорий не будет.
    """
    root = _kind_root(kind)
    if not root:
        return None
    cand = root / "maksim"
    return cand if cand.exists() else root


def scan_library(kind: str, category: str | None = None) -> list[dict]:
    """Файлы библиотеки ``kind`` (image/video), опц. в пределах ``category``.

    Returns ``[{"id", "path", "label"}, ...]``. ``id`` — стабильный хэш пути
    ОТНОСИТЕЛЬНО корня kind, поэтому lookup работает без знания категории.
    """
    root = _kind_root(kind)
    exts = _EXTS_BY_KIND.get(kind, ())
    if not root or not root.exists():
        return []
    base = (_brand_base(kind) / category) if category else root
    if not base or not base.exists():
        return []
    out: list[dict] = []
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            rel = p.relative_to(root).as_posix()
            iid = hashlib.md5(rel.encode("utf-8")).hexdigest()[:10]
            out.append({"id": iid, "path": str(p), "label": rel})
    return out


def list_library_categories(kind: str) -> list[tuple[str, int]]:
    """``[(category, count)]`` НЕПУСТЫХ категорий под ``<root>/<brand>/``.

    Пустые категории скрыты (Артём 10 июня: у Максима personal/team/meetings/
    nature/maksim_self пока пусты — нет смысла показывать тупиковые кнопки).
    """
    base = _brand_base(kind)
    if not base or not base.exists():
        return []
    exts = _EXTS_BY_KIND.get(kind, ())
    out: list[tuple[str, int]] = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        n = sum(1 for p in d.rglob("*") if p.is_file() and p.suffix.lower() in exts)
        if n > 0:
            out.append((d.name, n))
    return out


def list_library_sample(
    kind: str, category: str | None = None,
    n: int = 6, exclude_ids: list[str] | None = None,
) -> list[dict]:
    """Случайные n файлов из (категории) библиотеки, исключая показанные."""
    exclude = set(exclude_ids or [])
    pool = [c for c in scan_library(kind, category) if c["id"] not in exclude]
    if not pool:
        return []
    return random.sample(pool, min(n, len(pool)))


def lookup_library_path(kind: str, item_id: str) -> str | None:
    """Найти путь файла по ID во всей kind-библиотеке (для применения pick)."""
    for c in scan_library(kind, None):
        if c["id"] == item_id:
            return c["path"]
    return None


# ── back-compat тонкие обёртки (clip-only API, используется в других местах) ──
def _scan_clip_library() -> list[dict]:
    return scan_library("video", None)


def list_clip_library_sample(n: int = 6, exclude_ids: list[str] | None = None) -> list[dict]:
    return list_library_sample("video", None, n, exclude_ids)


def lookup_clip_path(clip_id: str) -> str | None:
    return lookup_library_path("video", clip_id)


def build_category_keyboard(
    kind: Literal["image", "video"],
    categories: list[tuple[str, int]],
) -> InlineKeyboardMarkup:
    """Подменю выбора категории библиотеки (только непустые) + назад."""
    src_tag = "photo" if kind == "image" else "clip"
    rows: list[list[InlineKeyboardButton]] = []
    for cat, n in categories:
        rows.append([InlineKeyboardButton(
            f"{_cat_label(cat)} ({n})",
            callback_data=f"selfie_broll:cat:{src_tag}:{cat}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="selfie_broll:back")])
    return InlineKeyboardMarkup(rows)


def build_toggle_keyboard(
    samples: list[dict],
    kind: Literal["image", "video"],
    category: str,
    selected_ids: set,
    total_count: int,
) -> InlineKeyboardMarkup:
    """Рич-picker: кнопки-цифры под media-превью (✅ на выбранных) + reroll +
    к категориям (выбор сохраняется) + Готово(N). Мультивыбор с накоплением,
    как в потоке «Фото к TG-посту» (Артём 8 июня)."""
    src_tag = "photo" if kind == "image" else "clip"
    toggle_row: list[InlineKeyboardButton] = []
    for i, s in enumerate(samples, start=1):
        sid = s["id"]
        mark = "✅" if sid in selected_ids else str(i)
        toggle_row.append(InlineKeyboardButton(
            mark, callback_data=f"selfie_broll:tog:{src_tag}:{category}:{sid}",
        ))
    rows = [toggle_row[:3], toggle_row[3:]] if len(toggle_row) > 3 else [toggle_row]
    rows.append([InlineKeyboardButton(
        "🔄 Ещё 6", callback_data=f"selfie_broll:reroll:{src_tag}:{category}")])
    rows.append([InlineKeyboardButton(
        "⬅️ К категориям", callback_data=f"selfie_broll:catback:{src_tag}")])
    rows.append([InlineKeyboardButton(
        f"✅ Готово ({total_count} выбрано)", callback_data="selfie_broll:done")])
    return InlineKeyboardMarkup(rows)


def build_library_keyboard(
    samples: list[dict],
    kind: Literal["image", "video"],
    category: str | None = None,
) -> InlineKeyboardMarkup:
    """6 кнопок выбора из библиотеки + reroll (в пределах категории) + назад.

    ``category`` пробрасывается в reroll, чтобы «Ещё 6» оставались в той же
    категории. «Назад» ведёт обратно в подменю категорий.
    """
    src_tag = "photo" if kind == "image" else "clip"
    rows: list[list[InlineKeyboardButton]] = []
    for s in samples:
        sid = s["id"]
        label = s.get("label") or sid
        rows.append([InlineKeyboardButton(
            label[:60],
            callback_data=f"selfie_broll:pick:{src_tag}:{sid}",
        )])
    reroll_cb = (
        f"selfie_broll:reroll:{src_tag}:{category}" if category
        else f"selfie_broll:reroll:{src_tag}"
    )
    rows.append([
        InlineKeyboardButton("🔄 Ещё 6", callback_data=reroll_cb),
        InlineKeyboardButton("⬅️ Назад", callback_data=f"selfie_broll:catback:{src_tag}"),
    ])
    return InlineKeyboardMarkup(rows)
