"""TDD: экран порядка клипов B-roll (Part A) — реордер перед сборкой.

Проверяем чистую логику без async-обвязки:
  * _reorder_swap — корректный обмен соседних, no-op у краёв;
  * _broll_reorder_keyboard — на каждый клип ⬆/⬇ + сборка/отмена, callback'и;
  * _broll_reorder_text — клипы в текущем порядке.

Запуск: python -m pytest tests/test_broll_reorder.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import broll.handlers as bh  # noqa: E402


class _It:
    def __init__(self, kind, path):
        self.kind, self.path = kind, path


class _Draft:
    def __init__(self, items):
        self.source_items = items


def _items():
    return [_It("video", "/x/up_001.mp4"), _It("image", "/x/up_002.jpg"),
            _It("video", "/x/up_003.mp4")]


# ── _reorder_swap ────────────────────────────────────────────────────────

def test_swap_down_mid():
    it = _items()
    assert bh._reorder_swap(it, "d", 1) is True
    assert [Path(x.path).name for x in it] == ["up_002.jpg", "up_001.mp4", "up_003.mp4"]


def test_swap_up_mid():
    it = _items()
    assert bh._reorder_swap(it, "u", 3) is True
    assert [Path(x.path).name for x in it] == ["up_001.mp4", "up_003.mp4", "up_002.jpg"]


def test_swap_up_first_is_noop():
    it = _items()
    assert bh._reorder_swap(it, "u", 1) is False
    assert [Path(x.path).name for x in it] == ["up_001.mp4", "up_002.jpg", "up_003.mp4"]


def test_swap_down_last_is_noop():
    it = _items()
    assert bh._reorder_swap(it, "d", 3) is False
    assert [Path(x.path).name for x in it] == ["up_001.mp4", "up_002.jpg", "up_003.mp4"]


def test_swap_out_of_range_noop():
    it = _items()
    assert bh._reorder_swap(it, "d", 99) is False
    assert bh._reorder_swap(it, "u", 0) is False


# ── клавиатура реордера ──────────────────────────────────────────────────

def test_reorder_keyboard_callbacks():
    kb = bh._broll_reorder_keyboard(_Draft(_items()))
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    # по ⬆/⬇ на каждый из 3 клипов
    for i in (1, 2, 3):
        assert f"b2up_move:u:{i}" in cbs and f"b2up_move:d:{i}" in cbs
    assert "b2up_assemble" in cbs, "нет кнопки сборки в выбранном порядке"
    assert "b2up_cancel" in cbs


def test_reorder_text_lists_in_order():
    txt = bh._broll_reorder_text(_Draft(_items()))
    p1 = txt.index("up_001.mp4")
    p2 = txt.index("up_002.jpg")
    p3 = txt.index("up_003.mp4")
    assert p1 < p2 < p3, "клипы не в текущем порядке"
    assert "🎬 видео" in txt and "🖼 фото" in txt


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
