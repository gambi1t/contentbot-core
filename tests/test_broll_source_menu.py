"""Тест меню источников + фоллбэк-логики Pipeline 2 (13 июня).

CTO-ревью Q1: плоское меню с time-labels (решение Артёма) — честно показывает
время каждого режима, снижает тревогу/повторные клики. Callback несёт draft_id
(stale-guard по status, не CAS). Q7: HF-only fail НЕ собирать молча из живых —
предложить выбор; auto_hf fail → тихо живые.

Запуск: python tests/test_broll_source_menu.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from broll.draft import SourceMode  # noqa: E402
from broll.source_menu import (  # noqa: E402
    source_menu_keyboard, parse_source_cb, hf_fallback_action,
)


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    did = "broll_123_1781000000000"

    print("\n[source_menu_keyboard — плоское, 5 режимов + time-labels]")
    kb = source_menu_keyboard(did)
    flat = [b for row in kb.inline_keyboard for b in row]
    cbs = [b.callback_data for b in flat]
    texts = " ".join(b.text for b in flat)
    for mode in SourceMode.ALL:
        _assert(any(f"b2src:{mode}:" in c for c in cbs),
                f"есть кнопка режима {mode}", errors)
    _assert(all(did in c for c in cbs if c.startswith("b2src:")),
            "каждый callback несёт draft_id (stale-guard)", errors)
    _assert("мин" in texts.lower(), "time-labels присутствуют (минуты)", errors)
    _assert(any("отмен" in b.text.lower() for b in flat), "есть Отмена", errors)

    print("\n[source_menu_keyboard — фазовая выкатка (подмножество режимов)]")
    kb1 = source_menu_keyboard(did, enabled_modes=[SourceMode.AUTO])
    flat1 = [b for row in kb1.inline_keyboard for b in row]
    cbs1 = [b.callback_data for b in flat1]
    _assert(any(f"b2src:{SourceMode.AUTO}:" in c for c in cbs1), "AUTO показан", errors)
    _assert(not any(f"b2src:{SourceMode.HF_ONLY}:" in c for c in cbs1),
            "выключенные режимы скрыты", errors)
    _assert(any("cancel" in c for c in cbs1), "Отмена есть всегда", errors)

    print("\n[parse_source_cb — разбор callback]")
    mode, draft_id = parse_source_cb(f"b2src:{SourceMode.HF_ONLY}:{did}")
    _assert(mode == SourceMode.HF_ONLY and draft_id == did,
            "разобран режим + draft_id", errors)
    _assert(parse_source_cb("b2src:bogus:x") == (None, None),
            "невалидный режим → (None, None)", errors)
    _assert(parse_source_cb("мусор") == (None, None), "мусор → (None, None)", errors)

    print("\n[hf_fallback_action — Q7]")
    # HF-only полностью упал → НЕ собирать молча, предложить выбор
    act = hf_fallback_action(SourceMode.HF_ONLY, hf_ok_count=0, live_available=True)
    _assert(act == "offer_choice",
            f"hf_only fail → выбор юзеру (не молча live), got {act}", errors)
    # HF-only частично (>=3 сцены) → можно собрать из того что есть
    act = hf_fallback_action(SourceMode.HF_ONLY, hf_ok_count=4, live_available=True)
    _assert(act == "proceed_partial", f"hf_only ≥3 → частичная сборка, got {act}", errors)
    # auto_hf: HF упал, но живые есть → тихо живые
    act = hf_fallback_action(SourceMode.AUTO_HF, hf_ok_count=0, live_available=True)
    _assert(act == "live_only", f"auto_hf fail + live → тихо живые, got {act}", errors)
    # auto_hf: HF упал и живых нет → фейл
    act = hf_fallback_action(SourceMode.AUTO_HF, hf_ok_count=0, live_available=False)
    _assert(act == "fail", f"auto_hf fail + нет live → fail, got {act}", errors)

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
