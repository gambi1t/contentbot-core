"""Сборка pro-монтажа карточки Максима из готовой папки проекта —
тот же путь, что у кнопки «Автосборка» (layout=pro + bookend-план).

Запуск НА СЕРВЕРЕ:  python _assemble_card_montage.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from video_assembler import assemble_auto_montage, build_bookend_montage_plan

PROJECTS = Path("/home/maksim-bot/maksim-bot/projects")


def main() -> int:
    matches = sorted(PROJECTS.glob("3656889c*"))
    if not matches:
        print("FAIL: папка проекта 3656889c* не найдена")
        return 1
    proj = matches[0]
    print(f"Проект: {proj.name}")

    avatars = sorted(proj.glob("avatar_*.mp4"))
    brolls = sorted(proj.glob("broll_*.mp4"))
    if not avatars or len(brolls) < 1:
        print(f"FAIL: avatar={len(avatars)}, broll={len(brolls)}")
        return 1

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(avatars[0])],
        capture_output=True, text=True,
    )
    dur = float(json.loads(probe.stdout)["format"]["duration"])
    plan = build_bookend_montage_plan(dur, len(brolls))
    print(f"avatar={avatars[0].name} ({dur:.1f}с) | broll={len(brolls)} | "
          f"сегментов={len(plan)}")

    out = assemble_auto_montage(
        proj, layout="pro", montage_plan=plan,
        subtitles=False, brand_name="maksim",
    )
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"OK: {out} — {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
