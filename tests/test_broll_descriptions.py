"""Тест build_broll_descriptions (8 июня): реальные описания клипов в монтаж.

Читает .json-сайдкар рядом с item.source → description. Порядок [видео, фото]
(под broll_paths = video_paths + photo_clips). Fallback на label/stem без сайдкара.

Запуск: python tests/test_broll_descriptions.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie.broll_picker import build_broll_descriptions, BrollItem  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    tmp = Path(tempfile.mkdtemp(prefix="brolldesc_"))
    # видео с сайдкаром
    v1 = tmp / "clip1.mov"; v1.write_bytes(b"x")
    json.dump({"description": "костёр ночью в глэмпинге"},
              open(str(v1) + ".json", "w", encoding="utf-8"))
    # видео без сайдкара → fallback
    v2 = tmp / "clip2.mov"; v2.write_bytes(b"x")
    # фото с сайдкаром
    p1 = tmp / "ph1.jpg"; p1.write_bytes(b"x")
    json.dump({"description": "улыбающаяся семья у домика"},
              open(str(p1) + ".json", "w", encoding="utf-8"))

    # вход в порядке [фото, видео, видео] — должно переставиться в [видео, видео, фото]
    items = [
        BrollItem("image", p1, "library/ph"),
        BrollItem("video", v1, "library/c1"),
        BrollItem("video", v2, "library/c2"),
    ]
    descs = build_broll_descriptions(items)

    print("\n[порядок и содержимое]")
    _assert(len(descs) == 3, f"3 описания, got {len(descs)}", errors)
    _assert("костёр" in descs[0], f"descs[0]=видео с сайдкаром, got {descs[0]!r}", errors)
    _assert("clip2" in descs[1], f"descs[1]=видео без сайдкара → stem, got {descs[1]!r}", errors)
    _assert("семья" in descs[2], f"descs[2]=фото с сайдкаром, got {descs[2]!r}", errors)

    print("\n[пометки вид/длительность]")
    _assert("видео" in descs[0] and "видео" in descs[1], "видео помечены «видео»", errors)
    _assert("фото" in descs[2], "фото помечено «фото»", errors)

    print("\n[без сайдкара берёт описание не из .json]")
    _assert("костёр" not in descs[1] and "семья" not in descs[1],
            "fallback не подтянул чужое описание", errors)

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
