"""Тест рич B-roll picker (8 июня): build_toggle_keyboard + _selected_lib_ids.

Мультивыбор toggle с накоплением: ✅ на выбранных, счётчик «Готово (N)»,
reroll/катбэк/done callbacks, выбор не теряется между категориями.

Запуск: python tests/test_broll_toggle.py
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

from selfie import broll_picker as bp  # noqa: E402
from selfie import handlers as sh  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _flat(kb):
    return [b for row in kb.inline_keyboard for b in row]


def main():
    errors = []

    print("\n[build_toggle_keyboard — ✅ на выбранных + счётчик]")
    samples = [{"id": f"id{i}"} for i in range(6)]
    selected = {"id1", "id3"}
    kb = bp.build_toggle_keyboard(samples, "image", "glamping", selected, total_count=4)
    flat = _flat(kb)
    cbs = [b.callback_data for b in flat]
    texts = [b.text for b in flat]
    # toggle callbacks для всех 6
    tog = [c for c in cbs if c.startswith("selfie_broll:tog:photo:glamping:")]
    _assert(len(tog) == 6, f"6 toggle-кнопок, got {len(tog)}", errors)
    # ✅ ровно на 2 выбранных
    _assert(texts.count("✅") == 2, f"✅ на 2 выбранных, got {texts.count('✅')}", errors)
    _assert("selfie_broll:tog:photo:glamping:id1" in cbs, "toggle id1 callback", errors)
    _assert(any("Готово (4 выбрано)" in t for t in texts), f"счётчик Готово(4), got {texts}", errors)
    _assert("selfie_broll:reroll:photo:glamping" in cbs, "reroll callback", errors)
    _assert("selfie_broll:catback:photo" in cbs, "катбэк к категориям", errors)
    _assert("selfie_broll:done" in cbs, "Готово → done", errors)

    print("\n[клипы → tog:clip]")
    kb2 = bp.build_toggle_keyboard([{"id": "v0"}], "video", "karting", set(), 0)
    cbs2 = [b.callback_data for b in _flat(kb2)]
    _assert("selfie_broll:tog:clip:karting:v0" in cbs2, "клип-toggle callback (clip)", errors)

    print("\n[_selected_lib_ids — из selfie_broll_items по label library/<id>]")
    data = {"selfie_broll_items": [
        {"kind": "image", "source": "/x/a.jpg", "label": "library/aaa"},
        {"kind": "video", "source": "/x/b.mov", "label": "library/bbb"},
        {"kind": "video", "source": "/x/up.mov", "label": "[AI] gen_01"},  # не библиотечный
    ]}
    sel = sh._selected_lib_ids(data)
    _assert(sel == {"aaa", "bbb"}, f"только библиотечные id, got {sel}", errors)
    _assert(sh._selected_lib_ids({}) == set(), "пусто → set()", errors)

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
