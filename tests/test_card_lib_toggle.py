"""Тест карточной toggle-клавиатуры библиотеки клипов (рич-формат, 8 июня).

_card_lib_toggle_keyboard: цифры → cbroll_tog:<gi>, ✅ на выбранных, счётчик в
кнопке «Сохранить», навигация (другая категория / меню). Мультивыбор по
глобальным индексам broll_clips.

Запуск: python tests/test_card_lib_toggle.py
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

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    shown = [3, 7, 9]  # глобальные индексы broll_clips

    print("\n[без выбора]")
    kb = bot._card_lib_toggle_keyboard(shown, set(), "glamping")
    flat = [b for row in kb.inline_keyboard for b in row]
    cbs = [b.callback_data for b in flat]
    _assert("cbroll_tog:3" in cbs and "cbroll_tog:7" in cbs and "cbroll_tog:9" in cbs,
            f"цифры → cbroll_tog по глоб. индексам: {cbs}", errors)
    nums = [b.text for b in flat if b.callback_data and b.callback_data.startswith("cbroll_tog:")]
    _assert(nums == ["1", "2", "3"], f"метки 1,2,3 когда не выбрано: {nums}", errors)
    save = [b for b in flat if b.callback_data == "broll_approve"]
    _assert(save and "(0)" in save[0].text, f"кнопка Сохранить (0): {save[0].text if save else None}", errors)
    _assert(any(c == "broll_local_lib" for c in cbs), "есть «Другая категория»", errors)
    _assert(any(c == "broll" for c in cbs), "есть «К меню B-roll»", errors)

    print("\n[с выбором — ✅ + счётчик]")
    kb2 = bot._card_lib_toggle_keyboard(shown, {7}, "glamping")
    flat2 = [b for row in kb2.inline_keyboard for b in row]
    marks = {b.callback_data: b.text for b in flat2 if b.callback_data and b.callback_data.startswith("cbroll_tog:")}
    _assert(marks.get("cbroll_tog:7") == "✅", f"выбранный (gi=7) → ✅: {marks}", errors)
    _assert(marks.get("cbroll_tog:3") == "1", "невыбранный → номер", errors)
    save2 = [b for b in flat2 if b.callback_data == "broll_approve"]
    _assert(save2 and "(1)" in save2[0].text, f"счётчик (1): {save2[0].text if save2 else None}", errors)

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
