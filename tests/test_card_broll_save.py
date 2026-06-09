"""Тест карточного B-roll save (Fix A+B, 9 июня).

Fix A: выбранные клипы карточной библиотеки реально копируются в проект
(broll_*.mp4 / photos/) через selfie.prepare_broll_in_project — раньше кнопка
«Сохранить» (broll_approve) была занята draft-системой и молча теряла выбор.
Fix B: ассемблер/план видят И видео, И фото проекта (фото-only → не «аватар»).

Запуск: python tests/test_card_broll_save.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie.broll_picker import BrollItem, prepare_broll_in_project  # noqa: E402
import video_assembler as va  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    tmp = Path(tempfile.mkdtemp(prefix="cbsave_"))
    src = tmp / "src"; src.mkdir()
    proj = tmp / "proj"; proj.mkdir()
    # исходные «выбранные» файлы: 2 видео + 1 фото (как из карточной библиотеки)
    v1 = src / "clip1.mov"; v1.write_bytes(b"v1")
    v2 = src / "clip2.mp4"; v2.write_bytes(b"v2")
    p1 = src / "photo1.jpg"; p1.write_bytes(b"p1")

    print("\n[Fix A — сохранение выбранного в проект]")
    # как cbroll_save: строим BrollItem по выбору + prepare_broll_in_project
    PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")
    items = []
    for f in (v1, v2, p1):
        kind = "image" if f.suffix.lower() in PHOTO_EXTS else "video"
        items.append(BrollItem(kind=kind, source=f, label=f.name))
    prepare_broll_in_project(items, proj)

    vids = sorted(proj.glob("broll_*.mp4"))
    _assert(len(vids) == 2, f"2 видео скопированы как broll_*.mp4, got {len(vids)}", errors)
    photos_dir = proj / "photos"
    pics = sorted(photos_dir.glob("photo_*.*")) if photos_dir.exists() else []
    _assert(len(pics) == 1, f"1 фото скопировано в photos/, got {len(pics)}", errors)

    print("\n[Fix B — ассемблер видит И видео, И фото]")
    found_v = va._find_broll(proj, mode="real")
    _assert(len(found_v) == 2, f"_find_broll нашёл 2 видео, got {len(found_v)}", errors)
    found_p = va._find_project_photos(proj)
    _assert(len(found_p) == 1, f"_find_project_photos нашёл 1 фото, got {len(found_p)}", errors)
    # план карточного пути считает видео+фото:
    _plan_count = len(found_v) + len(found_p)
    _assert(_plan_count == 3, f"план учитывает видео+фото = 3 (раньше было 2, фото игнор), got {_plan_count}", errors)

    print("\n[фото-only кейс — план НЕ нулевой (был баг «только аватар»)]")
    proj2 = tmp / "proj2"; proj2.mkdir()
    prepare_broll_in_project([BrollItem(kind="image", source=p1, label="p")], proj2)
    n_v2 = len(va._find_broll(proj2, mode="real"))
    n_p2 = len(va._find_project_photos(proj2))
    _assert(n_v2 == 0 and n_p2 == 1, f"фото-only: 0 видео, 1 фото (got {n_v2}/{n_p2})", errors)
    _assert(n_v2 + n_p2 == 1, "план фото-only = 1 (не 0 → не «только аватар»)", errors)

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
