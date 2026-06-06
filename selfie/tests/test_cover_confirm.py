"""TDD for selfie.cover confirm-step keyboards (9 июня UX-фикс).

Bug: библиотека показывала текстовые ID вместо фото; кадр/выбор коммитились
без подтверждения. Фикс — единый confirm-шаг + send_photo для библиотеки.
Эти тесты покрывают новые клавиатуры (pure, без сети).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from selfie.cover import (
    confirm_keyboard,
    library_pick_keyboard,
    library_footer_keyboard,
)


def _flat(kb):
    return [b for row in kb.inline_keyboard for b in row]


# ── confirm_keyboard ─────────────────────────────────────────────────────────

def test_confirm_keyboard_has_confirm_and_reject():
    have = {b.callback_data for b in _flat(confirm_keyboard())}
    assert "selfie_cover:confirm" in have
    assert "selfie_cover:reject" in have


def test_confirm_keyboard_exactly_two_buttons():
    assert len(_flat(confirm_keyboard())) == 2


# ── library_pick_keyboard ────────────────────────────────────────────────────

def test_library_pick_keyboard_callback_carries_id():
    kb = library_pick_keyboard("glamping_42")
    btns = _flat(kb)
    assert len(btns) == 1
    assert btns[0].callback_data == "selfie_cover:lib_pick:glamping_42"


def test_library_pick_keyboard_label_has_checkmark():
    btns = _flat(library_pick_keyboard("x"))
    assert "✅" in btns[0].text


# ── library_footer_keyboard ──────────────────────────────────────────────────

def test_library_footer_has_reroll_and_back():
    have = {b.callback_data for b in _flat(library_footer_keyboard())}
    assert "selfie_cover:lib_reroll" in have
    assert "selfie_cover:back" in have


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
