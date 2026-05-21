"""Mirror staged clips to nox-maksim server.

Pipeline:
  1. Build local mirror at clips-to-upload/_server_mirror/ that exactly
     matches the server target layout.
  2. scp -r that mirror to server.
  3. chown to maksim-bot.

Server layout target:
  /home/maksim-bot/maksim-bot/broll-library/
    ├── photos/maksim/<cat>/   ← all .jpg + their .json sidecars
    └── clips/maksim/<cat>/    ← all .mov/.mp4 + their .json sidecars

Glamping variants (glamping, glamping_holiday, glamping_evening) merge
into a single `glamping/` on server.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOCAL_ROOT = Path(__file__).parent / "clips-to-upload"
MIRROR = LOCAL_ROOT / "_server_mirror"
SSH_KEY = "C:/Users/Dell/.ssh/id_ed25519"
SSH_TARGET = "root@89.167.89.133"
SERVER_ROOT = "/home/maksim-bot/maksim-bot/broll-library"

# (local_category, server_category)
CATEGORY_MAP = [
    ("sup", "sup"),
    ("karting", "karting"),
    ("glamping", "glamping"),
    ("glamping_holiday", "glamping"),
    ("glamping_evening", "glamping"),
    ("personal", "personal"),
]

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png"}


def main():
    # Clean previous mirror
    if MIRROR.exists():
        print(f"Clearing previous mirror: {MIRROR}")
        shutil.rmtree(MIRROR)
    MIRROR.mkdir(parents=True)

    # Create target subdirs
    for kind in ("photos", "clips"):
        for _, server_cat in CATEGORY_MAP:
            (MIRROR / kind / "maksim" / server_cat).mkdir(parents=True, exist_ok=True)

    stats = {"photos": 0, "clips": 0, "json": 0}
    for local_cat, server_cat in CATEGORY_MAP:
        src_photos = LOCAL_ROOT / local_cat / "photos"
        src_videos = LOCAL_ROOT / local_cat / "videos"
        if src_photos.exists():
            for f in src_photos.iterdir():
                if not f.is_file():
                    continue
                ext = f.suffix.lower()
                if ext == ".heic":
                    continue  # was already converted
                if ext in PHOTO_EXTS or ext == ".json":
                    target = MIRROR / "photos" / "maksim" / server_cat / f.name
                    shutil.copy2(f, target)
                    if ext == ".json":
                        stats["json"] += 1
                    else:
                        stats["photos"] += 1
        if src_videos.exists():
            for f in src_videos.iterdir():
                if not f.is_file():
                    continue
                ext = f.suffix.lower()
                if ext in VIDEO_EXTS or ext == ".json":
                    target = MIRROR / "clips" / "maksim" / server_cat / f.name
                    shutil.copy2(f, target)
                    if ext == ".json":
                        stats["json"] += 1
                    else:
                        stats["clips"] += 1

    print(f"\nMirror built:")
    print(f"  photos: {stats['photos']}")
    print(f"  clips:  {stats['clips']}")
    print(f"  json:   {stats['json']}")

    total_size = sum(
        f.stat().st_size for f in MIRROR.rglob("*") if f.is_file()
    )
    print(f"  size:   {total_size / (1024**2):.1f} MB")

    # Show per-category breakdown
    print(f"\nPer-category:")
    for kind in ("photos", "clips"):
        for _, sc in CATEGORY_MAP:
            d = MIRROR / kind / "maksim" / sc
            files = list(d.iterdir())
            media = [f for f in files if f.suffix.lower() != ".json"]
            jsons = [f for f in files if f.suffix.lower() == ".json"]
            if media or jsons:
                print(f"  {kind}/maksim/{sc:15} media={len(media):3}  json={len(jsons):3}")

    # scp -r the mirror contents into broll-library
    print(f"\nUploading to server...")
    cmd = [
        "scp", "-i", SSH_KEY, "-r",
        str(MIRROR / "photos") + "/",
        str(MIRROR / "clips") + "/",
        f"{SSH_TARGET}:{SERVER_ROOT}/",
    ]
    print("  " + " ".join(cmd[:5]) + " ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n✗ scp failed (rc={result.returncode}):")
        print(result.stderr[-1500:])
        return 1
    print("  scp completed")

    # Fix ownership + inventory
    print(f"\nFixing ownership + checking inventory...")
    check_cmd = [
        "ssh", "-i", SSH_KEY, SSH_TARGET,
        f"""
chown -R maksim-bot:maksim-bot {SERVER_ROOT}/photos/maksim/ {SERVER_ROOT}/clips/maksim/ &&
echo 'Inventory:' &&
for cat in sup karting glamping personal; do
  p=$(find {SERVER_ROOT}/photos/maksim/$cat -maxdepth 1 -type f ! -name '*.json' 2>/dev/null | wc -l)
  pj=$(find {SERVER_ROOT}/photos/maksim/$cat -maxdepth 1 -name '*.json' 2>/dev/null | wc -l)
  v=$(find {SERVER_ROOT}/clips/maksim/$cat -maxdepth 1 -type f ! -name '*.json' 2>/dev/null | wc -l)
  vj=$(find {SERVER_ROOT}/clips/maksim/$cat -maxdepth 1 -name '*.json' 2>/dev/null | wc -l)
  echo "  $cat: photos=$p (json:$pj)  clips=$v (json:$vj)"
done
echo 'Total size:'
du -sh {SERVER_ROOT}/photos/maksim {SERVER_ROOT}/clips/maksim
echo 'Disk free:'
df -h /home | tail -1
"""
    ]
    result = subprocess.run(check_cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"stderr: {result.stderr}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
