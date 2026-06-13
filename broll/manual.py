"""Ручной пикер библиотеки для Pipeline 2 (срез 3, 13 июня).

Режим «👆 Вручную»: мультивыбор клипов из библиотеки. Переиспользует листинг
selfie/broll_picker (list_library_sample/lookup_library_path/categories), но
со своими callbacks (b2man:*), чтобы не пересекаться с селфи-пикером. Чистые
помощники тут; оркестрация (показ превью, состояние выбора) — в handlers.py.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .draft import BrollItem

_CB = "b2man"


def parse_b2man_cb(data: str) -> tuple:
    """'b2man:<action>[:<cat>[:<id>]]' → (action, cat|None, id|None)."""
    parts = (data or "").split(":")
    if len(parts) < 2 or parts[0] != _CB:
        return (None, None, None)
    action = parts[1]
    cat = parts[2] if len(parts) > 2 else None
    item_id = parts[3] if len(parts) > 3 else None
    return (action, cat, item_id)


def manual_toggle_keyboard(samples, category: str, selected_ids,
                           total: int) -> InlineKeyboardMarkup:
    """Тоггл-клавиатура: ✅ на выбранных + Ещё/К категориям/Готово(N).
    Структура зеркалит selfie build_toggle_keyboard, callbacks — b2man."""
    row = []
    for i, s in enumerate(samples, start=1):
        sid = s["id"]
        mark = "✅" if sid in selected_ids else str(i)
        row.append(InlineKeyboardButton(
            mark, callback_data=f"{_CB}:tog:{category}:{sid}"))
    rows = [row[:3], row[3:]] if len(row) > 3 else [row]
    rows.append([InlineKeyboardButton("🔄 Ещё 6", callback_data=f"{_CB}:reroll:{category}")])
    rows.append([InlineKeyboardButton("⬅️ К категориям", callback_data=f"{_CB}:cats")])
    rows.append([InlineKeyboardButton(
        f"✅ Готово ({total} выбрано)", callback_data=f"{_CB}:done")])
    return InlineKeyboardMarkup(rows)


def manual_categories_keyboard(categories) -> InlineKeyboardMarkup:
    """Категории клипов библиотеки (label, count) → кнопки b2man:cat."""
    from selfie.broll_picker import _cat_label
    rows = [[InlineKeyboardButton(
        f"{_cat_label(cat)} ({n})", callback_data=f"{_CB}:cat:{cat}")]
        for cat, n in categories]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="b2up_cancel")])
    return InlineKeyboardMarkup(rows)


def manual_items_from_ids(selected_ids, lookup_fn) -> list[BrollItem]:
    """id выбранных клипов → BrollItem(origin=library). lookup_fn(kind,id)→path
    (инъекция для тестов; в проде selfie.broll_picker.lookup_library_path)."""
    out: list[BrollItem] = []
    for sid in selected_ids:
        path = lookup_fn("video", sid)
        if path:
            out.append(BrollItem(
                kind="video", origin="library", path=str(path),
                label=f"library/{sid}"))
    return out
