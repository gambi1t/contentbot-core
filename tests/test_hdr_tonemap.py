"""Тест HDR-детекта для фикса «красноты лица» (8 июня).

B-roll библиотеки — часто 4K HDR (bt2020/HLG); без тонмаппинга в SDR сегменты
с B-roll краснят. _is_hdr должен ловить bt2020/HLG/PQ и НЕ ловить bt709.

Запуск: python tests/test_hdr_tonemap.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import video_assembler as va  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[константы заданы]")
    _assert("bt709" in " ".join(va._SDR_COLOR_TAGS), "теги содержат bt709", errors)
    _assert("color_range" in " ".join(va._SDR_COLOR_TAGS), "теги задают color_range", errors)
    _assert("tonemap" in va._HDR_TONEMAP and "zscale" in va._HDR_TONEMAP,
            "цепочка тонмаппинга содержит zscale+tonemap", errors)

    print("\n[_is_hdr — bt709 SDR → False]")
    tmp = Path(tempfile.mkdtemp())
    sdr = tmp / "sdr.mp4"
    # сгенерим короткий bt709 SDR клип через ffmpeg testsrc
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         str(sdr)],
        capture_output=True, text=True, timeout=60,
    )
    if sdr.exists():
        _assert(va._is_hdr(sdr) is False, "bt709 клип → не HDR (False)", errors)
    else:
        print(f"  ⚠ ffmpeg не создал тестовый клип ({r.stderr[-200:]}) — пропуск")

    print("\n[_is_hdr — несуществующий файл не падает]")
    _assert(va._is_hdr(tmp / "nope.mp4") is False, "несуществующий → False (без краша)", errors)

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
