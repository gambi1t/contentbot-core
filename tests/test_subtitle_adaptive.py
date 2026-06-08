"""Тест адаптивных субтитров в монтаже (8 июня).

Субтитры должны быть видны ВСЕГДА и перемещаться по лейауту сегмента:
split → стык (MarginV=900, середина), fullscreen/avatar → низ (480).
Проверяем _margin_for_word + что generate_ass пишет разные MarginV по словам.

Запуск: python tests/test_subtitle_adaptive.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import subtitle_burner as sb  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    plan = [
        {"start": 0.0, "end": 2.5, "layout": "avatar_full"},
        {"start": 2.5, "end": 8.0, "layout": "split"},
        {"start": 8.0, "end": 12.0, "layout": "broll_full"},
        {"start": 12.0, "end": 15.0, "layout": "avatar_full"},
    ]

    print("\n[_margin_for_word — позиция по лейауту]")
    _assert(sb._margin_for_word(1.0, plan) == 480, "avatar_full → низ (480)", errors)
    _assert(sb._margin_for_word(5.0, plan) == 900, "split → стык (900)", errors)
    _assert(sb._margin_for_word(9.0, plan) == 480, "broll_full → низ (480)", errors)
    _assert(sb._margin_for_word(13.0, plan) == 480, "avatar_full (CTA) → низ (480)", errors)
    _assert(sb._margin_for_word(5.0, None) == 0, "без плана → стиль по умолчанию (0)", errors)

    print("\n[generate_ass — разные MarginV по словам]")
    words = [
        {"word": "привет", "start": 1.0, "end": 1.4},   # avatar → 480
        {"word": "смотри", "start": 5.0, "end": 5.4},    # split → 900
        {"word": "это", "start": 9.0, "end": 9.3},       # broll → 480
    ]
    out = Path(tempfile.mkdtemp()) / "t.ass"
    sb.generate_ass(words, out, montage_plan=plan)
    text = out.read_text(encoding="utf-8")
    # В ASS строках Dialogue MarginV — 8-е поле. Проверяем, что 900 и 480 оба есть.
    _assert(",900,," in text or ",900," in text, "в ASS есть MarginV=900 (split-слово)", errors)
    _assert(",480,," in text or ",480," in text, "в ASS есть MarginV=480 (fullscreen-слово)", errors)

    print("\n[add_subtitles_to_video — принимает готовые words]")
    import inspect
    sig = inspect.signature(sb.add_subtitles_to_video)
    _assert("words" in sig.parameters, "у add_subtitles_to_video есть параметр words", errors)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
