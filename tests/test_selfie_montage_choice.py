"""Тест выбора формата монтажа в /selfie (8 июня).

Чузер форматов (Смарт-микс/Сплит/Динамический/Про-монтаж/ИИ-монтаж) должен:
- содержать 5 форматов с правильными callback_data selfie_broll:asm:<code>;
- маппить код кнопки на layout как card-путь (s→split, d→dynamic, p/a→pro, m→smart);
- называть ИИ-монтаж через Claude (не Opus).

Запуск: python tests/test_selfie_montage_choice.py
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

from selfie import handlers as sh  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[_MONTAGE_FORMATS — 5 форматов]")
    _assert(set(sh._MONTAGE_FORMATS) == {"m", "s", "d", "p", "a"},
            f"5 кодов форматов, got {set(sh._MONTAGE_FORMATS)}", errors)
    _assert(tuple(sh._MONTAGE_ORDER) == ("m", "s", "d", "p", "a"),
            "порядок меню m,s,d,p,a", errors)

    print("\n[_SELFIE_LAYOUT_MAP — как card-путь]")
    expected = {"s": "split", "d": "dynamic", "p": "pro", "a": "pro", "m": "smart"}
    _assert(sh._SELFIE_LAYOUT_MAP == expected,
            f"маппинг код→layout, got {sh._SELFIE_LAYOUT_MAP}", errors)

    print("\n[_montage_format_keyboard — callback_data]")
    kb = sh._montage_format_keyboard()
    flat = [b for row in kb.inline_keyboard for b in row]
    cbs = [b.callback_data for b in flat]
    for c in ("m", "s", "d", "p", "a"):
        _assert(f"selfie_broll:asm:{c}" in cbs,
                f"есть кнопка selfie_broll:asm:{c}", errors)
    _assert(any(b.callback_data == "selfie_broll:back" for b in flat),
            "есть кнопка «Назад к B-roll»", errors)
    _assert(len(flat) == 6, f"5 форматов + назад = 6 кнопок, got {len(flat)}", errors)

    print("\n[ИИ-монтаж — через Claude, не Opus]")
    ai_name, ai_desc = sh._MONTAGE_FORMATS["a"]
    _assert("Claude" in ai_desc and "Opus" not in ai_desc,
            f"описание ИИ-монтажа упоминает Claude, не Opus: {ai_desc}", errors)

    print("\n[_montage_format_message]")
    msg = sh._montage_format_message(4)
    _assert("B-roll: 4" in msg, "в сообщении число B-roll", errors)
    for c in ("m", "s", "d", "p", "a"):
        _assert(sh._MONTAGE_FORMATS[c][0] in msg, f"в сообщении формат {c}", errors)

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
