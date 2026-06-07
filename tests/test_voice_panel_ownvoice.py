"""Тест: кнопка «🎤 Озвучить своим голосом» во ВСЕХ меню озвучки (11 июня).

Все панели озвучки строятся через bot._voice_panel_keyboard (ручные пересборы
в process_idea замаршрутизированы туда же). Проверяем, что кнопка
voiceover_ownvoice присутствует при любом числе частей и любом статусе approve.

Запуск: python tests/test_voice_panel_ownvoice.py
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

import bot  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _cbs(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def _texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def main():
    errors = []
    print("\n[_voice_panel_keyboard — кнопка «своим голосом» при любом числе частей]")
    cases = [
        {"voice_parts": [], "voice_approved": []},
        {"voice_parts": ["раз"], "voice_approved": [False]},
        {"voice_parts": ["раз", "два", "три"], "voice_approved": [True, True, True]},
        {"voice_parts": ["раз", "два"], "voice_approved": [True, False]},
    ]
    for c in cases:
        kb = bot._voice_panel_keyboard(c)
        cbs = _cbs(kb)
        txt = _texts(kb)
        n = len(c["voice_parts"])
        _assert("voiceover_ownvoice" in cbs,
                f"callback voiceover_ownvoice есть (частей={n}, approved={c['voice_approved']})", errors)
        _assert(any("своим голосом" in t.lower() for t in txt),
                f"текст «своим голосом» есть (частей={n})", errors)

    print("\n[_voiceover_choice_keyboard — развилка ИИ-голос / свой голос]")
    kb = bot._voiceover_choice_keyboard()
    cbs = _cbs(kb)
    _assert("voiceover" in cbs, "есть выбор ИИ-голос (callback voiceover)", errors)
    _assert("voiceover_ownvoice" in cbs, "есть выбор свой голос (callback voiceover_ownvoice)", errors)
    _assert(not any("voiceover" not in c for c in cbs),
            f"без лишних callback'ов, got {cbs}", errors)
    kb_back = bot._voiceover_choice_keyboard(back_cb="notion_card:abc")
    _assert("notion_card:abc" in _cbs(kb_back), "back_cb добавляет кнопку назад", errors)

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
