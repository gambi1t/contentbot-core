"""Regression lock: shoes split-anchor = 1.0 (порт M4).

История (7 июня 2026): для бренда shoes anchor 0.75/0.62 СРЕЗАЛИ обувь снизу на
кадрах Ken Burns (zoom 1.0→1.06). Геометрия (_split_visible_photo_band)
подтвердила: только anchor=1.0 держит обувь [0.60, 0.95] целиком на всех зумах.
Core однажды откатил на 0.75 («1.0 too aggressive») — этот замок ловит откат.

Run: python tests/test_split_anchor.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from video_assembler import (  # noqa: E402
    SHOES_SPLIT_ANCHOR,
    _split_visible_photo_band,
    _shoe_anchor_keeps_shoe_visible,
)

_errs: list[str] = []

# Типичная зона обуви на lifestyle-фото (доли высоты кадра).
_SHOE_TOP, _SHOE_BOT = 0.60, 0.95


def _assert(cond, msg):
    print(f"  {'OK' if cond else 'X FAIL'} {msg}")
    if not cond:
        _errs.append(msg)


def test_constant_is_1():
    print("\n-- SHOES_SPLIT_ANCHOR == 1.0 --")
    _assert(SHOES_SPLIT_ANCHOR == 1.0, f"anchor=1.0 (got {SHOES_SPLIT_ANCHOR})")


def test_anchor_1_keeps_shoe():
    print("\n-- anchor 1.0 держит обувь на всех зумах --")
    _assert(_shoe_anchor_keeps_shoe_visible(_SHOE_TOP, _SHOE_BOT, 1.0),
            "обувь [0.60,0.95] видна при anchor=1.0 (zoom 1.0 и 1.06)")


def test_lower_anchors_cut_shoe():
    print("\n-- регресс: 0.75/0.62 срезают обувь (НЕ держат) --")
    _assert(not _shoe_anchor_keeps_shoe_visible(_SHOE_TOP, _SHOE_BOT, 0.75),
            "anchor=0.75 срезает обувь снизу (как и было в проде)")
    _assert(not _shoe_anchor_keeps_shoe_visible(_SHOE_TOP, _SHOE_BOT, 0.62),
            "anchor=0.62 срезает обувь снизу")


def test_band_geometry():
    print("\n-- геометрия split-band (sanity) --")
    bt, bb = _split_visible_photo_band(1.0, 1.0)
    _assert(abs(bt - 0.5) < 1e-9 and abs(bb - 1.0) < 1e-9,
            f"anchor=1.0 zoom=1.0 → нижняя половина [0.5,1.0] (got [{bt:.3f},{bb:.3f}])")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"{'='*60}\nRunning {len(tests)} split-anchor lock tests\n{'='*60}")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _errs.append(f"{fn.__name__}: {e}")
            print(f"  X EXC {fn.__name__}: {e}")
    print(f"\n{'='*60}")
    print("ALL PASS" if not _errs else f"FAIL ({len(_errs)}): " + "; ".join(_errs))
    sys.exit(0 if not _errs else 1)
