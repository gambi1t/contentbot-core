"""TDD for selfie.broll_picker UI helpers (keyboards + message builder).

Visual structure (что показываем пользователю):

  Offer:
    🎬 Добавить B-roll? — [✅ Да, добавить B-roll] [➡️ Без B-roll, продолжить]

  Picker (после Да):
    🎬 B-roll: добавлено 2/7
       ✓ 1. фото из библиотеки (glamping)
       ✓ 2. видео загружено
    [📷 Фото из библиотеки]
    [🎞 Клипы из библиотеки]
    [📤 Загрузить своё фото]
    [📤 Загрузить своё видео]
    [🗑 Убрать последний]                  ← если items > 0
    [✅ Готово (2 выбрано)] [❌ Отмена]      ← Готово только когда items > 0

  Library photo picker:
    📷 Выбери фото из библиотеки:
    [photo_id_1] [photo_id_2] [photo_id_3]
    [🔄 Ещё 6] [⬅️ Назад]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from selfie.broll_picker import (
    BrollItem,
    build_offer_keyboard,
    build_picker_keyboard,
    build_picker_message,
    build_library_keyboard,
)


# ── build_offer_keyboard ─────────────────────────────────────────────────────

def test_offer_keyboard_has_two_buttons():
    kb = build_offer_keyboard()
    flat = [btn for row in kb.inline_keyboard for btn in row]
    assert len(flat) == 2


def test_offer_keyboard_add_callback():
    kb = build_offer_keyboard()
    flat = [btn for row in kb.inline_keyboard for btn in row]
    add_btns = [b for b in flat if b.callback_data == "selfie_broll:add"]
    assert len(add_btns) == 1


def test_offer_keyboard_skip_callback():
    kb = build_offer_keyboard()
    flat = [btn for row in kb.inline_keyboard for btn in row]
    skip_btns = [b for b in flat if b.callback_data == "selfie_broll:skip"]
    assert len(skip_btns) == 1


# ── build_picker_keyboard ────────────────────────────────────────────────────

def test_picker_keyboard_has_four_source_buttons():
    kb = build_picker_keyboard([])
    flat = [btn for row in kb.inline_keyboard for btn in row]
    sources = {"selfie_broll:lib_photo", "selfie_broll:lib_clip",
               "selfie_broll:upload_photo", "selfie_broll:upload_video"}
    have = {b.callback_data for b in flat}
    missing = sources - have
    assert not missing, f"missing sources: {missing}"


def test_picker_keyboard_empty_list_hides_done_and_remove():
    """С пустым списком нельзя «Готово» (нечего собирать) и нельзя удалить."""
    kb = build_picker_keyboard([])
    have = {b.callback_data for row in kb.inline_keyboard for b in row}
    assert "selfie_broll:done" not in have
    assert "selfie_broll:remove_last" not in have


def test_picker_keyboard_with_items_shows_done_and_remove():
    items = [BrollItem(kind="image", source=Path("/tmp/a.jpg"))]
    kb = build_picker_keyboard(items)
    have = {b.callback_data for row in kb.inline_keyboard for b in row}
    assert "selfie_broll:done" in have
    assert "selfie_broll:remove_last" in have


def test_picker_keyboard_done_label_shows_count():
    items = [
        BrollItem(kind="image", source=Path("/tmp/a.jpg")),
        BrollItem(kind="video", source=Path("/tmp/b.mp4")),
    ]
    kb = build_picker_keyboard(items)
    done_buttons = [
        b for row in kb.inline_keyboard for b in row
        if b.callback_data == "selfie_broll:done"
    ]
    assert "2" in done_buttons[0].text


def test_picker_keyboard_disables_uploads_when_at_limit():
    """На лимите — кнопки добавления заменяются предупреждением."""
    from selfie.broll_picker import MAX_BROLL_ITEMS
    items = [
        BrollItem(kind="image", source=Path(f"/tmp/{i}.jpg"))
        for i in range(MAX_BROLL_ITEMS)
    ]
    kb = build_picker_keyboard(items)
    have = {b.callback_data for row in kb.inline_keyboard for b in row}
    # Источники добавления больше не активны (на лимите можно только
    # удалить и/или Готово).
    sources = {
        "selfie_broll:lib_photo", "selfie_broll:lib_clip",
        "selfie_broll:upload_photo", "selfie_broll:upload_video",
    }
    assert not (sources & have), "Add-source buttons must be hidden at limit"
    assert "selfie_broll:done" in have
    assert "selfie_broll:remove_last" in have


def test_picker_keyboard_has_cancel_button():
    kb = build_picker_keyboard([])
    have = {b.callback_data for row in kb.inline_keyboard for b in row}
    assert "selfie_broll:cancel" in have


# ── build_picker_message ─────────────────────────────────────────────────────

def test_picker_message_empty():
    msg = build_picker_message([])
    assert "0" in msg or "пуст" in msg.lower() or "не выбра" in msg.lower()


def test_picker_message_counts():
    items = [
        BrollItem(kind="image", source=Path("/tmp/a.jpg"), label="glamping/g1"),
        BrollItem(kind="video", source=Path("/tmp/b.mp4")),
    ]
    msg = build_picker_message(items)
    assert "2" in msg
    # Хотя бы упоминание лимита 7 (UX-подсказка)
    assert "7" in msg


def test_picker_message_lists_kinds():
    items = [
        BrollItem(kind="image", source=Path("/tmp/a.jpg")),
        BrollItem(kind="video", source=Path("/tmp/b.mp4")),
    ]
    msg = build_picker_message(items)
    low = msg.lower()
    # В тексте упомянуты типы (фото/видео)
    assert "фото" in low
    assert "видео" in low


# ── build_library_keyboard ───────────────────────────────────────────────────

def test_library_keyboard_photo_kind_callbacks_have_correct_prefix():
    samples = [
        {"id": "g1", "path": "/lib/photo/glamping_1.jpg"},
        {"id": "g2", "path": "/lib/photo/glamping_2.jpg"},
    ]
    kb = build_library_keyboard(samples, kind="image")
    pick_btns = [
        b for row in kb.inline_keyboard for b in row
        if (b.callback_data or "").startswith("selfie_broll:pick:photo:")
    ]
    assert len(pick_btns) == 2


def test_library_keyboard_clip_kind_callbacks_have_correct_prefix():
    samples = [{"id": "k1", "path": "/lib/clip/karting_1.mp4"}]
    kb = build_library_keyboard(samples, kind="video")
    pick_btns = [
        b for row in kb.inline_keyboard for b in row
        if (b.callback_data or "").startswith("selfie_broll:pick:clip:")
    ]
    assert len(pick_btns) == 1


def test_library_keyboard_has_reroll_and_back():
    samples = [{"id": "g1", "path": "/lib/photo/g1.jpg"}]
    kb = build_library_keyboard(samples, kind="image")
    have = {b.callback_data for row in kb.inline_keyboard for b in row}
    assert "selfie_broll:reroll:photo" in have
    assert "selfie_broll:back" in have


def test_library_keyboard_back_reroll_use_clip_for_video_kind():
    samples = [{"id": "k1", "path": "/lib/clip/k1.mp4"}]
    kb = build_library_keyboard(samples, kind="video")
    have = {b.callback_data for row in kb.inline_keyboard for b in row}
    assert "selfie_broll:reroll:clip" in have


def test_library_keyboard_empty_samples():
    """Если в библиотеке пусто — должна быть кнопка Назад, без pick'ов."""
    kb = build_library_keyboard([], kind="image")
    have = {b.callback_data for row in kb.inline_keyboard for b in row}
    assert "selfie_broll:back" in have
    pick_btns = [
        c for c in have if (c or "").startswith("selfie_broll:pick:")
    ]
    assert not pick_btns


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
