"""Тест ручного пикера Pipeline 2 (срез 3, 13 июня).

Режим «👆 Вручную»: мультивыбор клипов из библиотеки. Переиспользует листинг
selfie/broll_picker (list_library_sample/lookup_library_path), но со своими
callbacks (b2man:*) и выбором в durable-flow. Чистые помощники тестируем тут;
оркестрацию — Telethon.

Запуск: python tests/test_broll_manual.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from broll.draft import BrollItem  # noqa: E402
from broll.manual import (  # noqa: E402
    manual_toggle_keyboard, parse_b2man_cb, manual_items_from_ids,
)


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[parse_b2man_cb — разбор callback]")
    _assert(parse_b2man_cb("b2man:cat:glamping") == ("cat", "glamping", None),
            "cat", errors)
    _assert(parse_b2man_cb("b2man:tog:glamping:id42") == ("tog", "glamping", "id42"),
            "tog с id", errors)
    _assert(parse_b2man_cb("b2man:reroll:karting") == ("reroll", "karting", None),
            "reroll", errors)
    _assert(parse_b2man_cb("b2man:done") == ("done", None, None), "done", errors)
    _assert(parse_b2man_cb("b2man:cats") == ("cats", None, None), "cats", errors)
    _assert(parse_b2man_cb("мусор") == (None, None, None), "мусор → None", errors)

    print("\n[manual_toggle_keyboard — ✅ на выбранных + Готово(N)]")
    samples = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    kb = manual_toggle_keyboard(samples, "glamping", {"b"}, total=1)
    flat = [btn for row in kb.inline_keyboard for btn in row]
    cbs = [b.callback_data for b in flat]
    texts = [b.text for b in flat]
    _assert("b2man:tog:glamping:a" in cbs and "b2man:tog:glamping:b" in cbs,
            "тоггл-кнопки с id", errors)
    _assert("✅" in texts, "выбранный помечен ✅", errors)
    _assert(any("Готово" in t and "1" in t for t in texts), "Готово(N) с числом", errors)
    _assert(any(c == "b2man:done" for c in cbs), "Готово callback", errors)
    _assert(any(c == "b2man:reroll:glamping" for c in cbs), "Ещё (reroll) callback", errors)
    _assert(any(c == "b2man:cats" for c in cbs), "К категориям callback", errors)

    print("\n[manual_items_from_ids — id → BrollItem(origin=library) через lookup]")
    paths = {"a": "/lib/karting/a.mp4", "c": "/lib/glamping/c.mov"}
    def fake_lookup(kind, item_id):
        return paths.get(item_id)
    items = manual_items_from_ids(["a", "c", "bad"], fake_lookup)
    _assert(len(items) == 2 and all(isinstance(x, BrollItem) for x in items),
            "найденные → BrollItem (битый id пропущен)", errors)
    _assert(all(x.origin == "library" and x.kind == "video" for x in items),
            "origin=library, kind=video", errors)
    _assert(items[0].path == "/lib/karting/a.mp4", "путь из lookup", errors)
    _assert(manual_items_from_ids([], fake_lookup) == [], "пустой → []", errors)

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
