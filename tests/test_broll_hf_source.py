"""Тест HF-источника Pipeline 2 (Фаза 2, 13 июня).

Режим «🎨 Только графика»: 6 готовых hf_NN.mp4 от generate_hyperframes_broll →
BrollItem(kind=hf_scene, origin=hf). Чистый конструктор тестируем тут;
генерацию (8-25 мин) и прогресс-мост — Telethon.

Запуск: python tests/test_broll_hf_source.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from broll.draft import BrollItem, hf_items_from_clips  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[hf_items_from_clips — пути hf_NN.mp4 → BrollItem(hf_scene/hf)]")
    clips = [Path("/proj/hyperframes/hf_01.mp4"),
             Path("/proj/hyperframes/hf_02.mp4")]
    items = hf_items_from_clips(clips)
    _assert(len(items) == 2 and all(isinstance(x, BrollItem) for x in items),
            "клипы → BrollItem", errors)
    _assert(all(x.kind == "hf_scene" for x in items), "kind=hf_scene", errors)
    _assert(all(x.origin == "hf" for x in items), "origin=hf", errors)
    _assert(items[0].path == str(Path("/proj/hyperframes/hf_01.mp4")),
            "путь сохранён строкой", errors)
    _assert("hf_01" in items[0].label, "метка содержит имя клипа", errors)

    print("\n[hf_items_from_clips — строки тоже принимаются]")
    items_s = hf_items_from_clips(["/p/hf_01.mp4"])
    _assert(len(items_s) == 1 and items_s[0].kind == "hf_scene",
            "str-путь → BrollItem", errors)

    print("\n[hf_items_from_clips — пустой/битый вход]")
    _assert(hf_items_from_clips([]) == [], "пустой → []", errors)
    _assert(hf_items_from_clips([None, "", "/p/hf_01.mp4"]) and
            len(hf_items_from_clips([None, "", "/p/hf_01.mp4"])) == 1,
            "None/'' пропущены", errors)

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
