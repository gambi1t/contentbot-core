"""Сборка тест-монтажа в ДВУХ вариантах для сравнения форматов.

Один материал (avatar_01.mp4 + 6 коротких B-roll-вставок broll_01..06.mp4):
  A. dynamic — попеременный full-screen (аватар фоном, B-roll вставки 3-5с)
  B. split   — 50/50 (B-roll сверху, аватар снизу)

project_dir _montage_test/ должен содержать:
  avatar_01.mp4         — talking-head (32.12с)
  broll_01..06.mp4      — короткие вставки (4с каждая)

Запуск НА СЕРВЕРЕ:  python _assemble_montage.py
Выход: _montage_test/final_dynamic.mp4 и final_split.mp4
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from video_assembler import assemble_auto_montage

PROJ = Path(__file__).parent / "_montage_test"


def main() -> int:
    if not (PROJ / "avatar_01.mp4").exists():
        print("FAIL: нет avatar_01.mp4")
        return 1
    brolls = sorted(PROJ.glob("broll_*.mp4"))
    print(f"B-roll вставок: {len(brolls)}")
    if len(brolls) < 3:
        print("FAIL: мало B-roll вставок")
        return 1

    # ── Вариант A: dynamic ──
    out_a = assemble_auto_montage(PROJ, layout="dynamic", subtitles=False,
                                  brand_name="default")
    final_a = PROJ / "final_dynamic.mp4"
    shutil.move(str(out_a), str(final_a))
    print(f"OK dynamic: {final_a} — {final_a.stat().st_size // 1024} KB")

    # ── Вариант B: split 50/50 ──
    out_b = assemble_auto_montage(PROJ, layout="split", subtitles=False,
                                  brand_name="default")
    final_b = PROJ / "final_split.mp4"
    shutil.move(str(out_b), str(final_b))
    print(f"OK split: {final_b} — {final_b.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
