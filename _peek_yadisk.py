"""Quick recon of public Yandex.Disk folders — list files + sizes + types.

For each public link, prints:
  - total file count and total size
  - breakdown by file type (.mp4, .mov, .jpg, .png, ...)
  - first 5 file names as a sample
  - red flags: weird formats, huge files (>500MB), zero files
"""
import sys
from pathlib import Path
import yadisk

# UTF-8 stdout on Windows
import io
if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCES = [
    ("sup",              "https://disk.yandex.ru/d/PFPhFvquRA-1yQ"),
    ("karting",          "https://disk.yandex.ru/d/B9rvARqs7rQetA"),
    ("glamping",         "https://disk.yandex.ru/d/r5P-pPwdGxi1Jw"),
    ("glamping_holiday", "https://disk.yandex.ru/d/VJ8t9Sg6fVggPw"),
    ("glamping_evening", "https://disk.yandex.ru/d/v9-Xb5TcDEmcQg"),
    ("personal",         "https://disk.yandex.ru/d/LaM6WPA-zYqfxA"),
]

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}

y = yadisk.YaDisk()


def walk_public(public_key: str):
    """Yield (name, path, size, type) for files (recursive across subdirs)."""
    try:
        meta = y.get_public_meta(public_key, limit=10000)
    except Exception as e:
        return None, f"get_public_meta failed: {e}", []

    items = []
    subdirs = []
    if meta.type == "file":
        items.append({
            "name": meta.name, "path": meta.path,
            "size": meta.size or 0, "media_type": meta.media_type,
            "mime_type": meta.mime_type, "rel_path": "/",
        })
        return items, None, subdirs

    # BFS recursive walk — `public_listdir(public_key, path="/sub")` lets us
    # descend into subfolders by relative path within the public resource.
    queue = [""]  # relative paths inside the public resource
    visited = set()
    while queue:
        rel = queue.pop(0)
        if rel in visited:
            continue
        visited.add(rel)
        try:
            # public_key is positional-only in yadisk 3.4+, path is kwarg
            if rel:
                iterator = y.public_listdir(public_key, path=rel, limit=10000)
            else:
                iterator = y.public_listdir(public_key, limit=10000)
            for item in iterator:
                if item.type == "file":
                    items.append({
                        "name": item.name, "path": item.path,
                        "size": item.size or 0,
                        "media_type": item.media_type,
                        "mime_type": item.mime_type,
                        "rel_path": rel or "/",
                    })
                elif item.type == "dir":
                    sub_rel = f"{rel}/{item.name}" if rel else f"/{item.name}"
                    subdirs.append(sub_rel)
                    queue.append(sub_rel)
        except Exception as e:
            return items, f"public_listdir({rel!r}) failed: {e}", subdirs
    return items, None, subdirs


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


print("=" * 70)
total_files = 0
total_size = 0
all_summaries = []

for label, link in SOURCES:
    print(f"\n[{label}]")
    print(f"  {link}")
    items, err, subdirs = walk_public(link)
    if err:
        print(f"  ✗ ERROR: {err}")
        continue

    if subdirs:
        print(f"  ℹ {len(subdirs)} subdirectories: {subdirs[:6]}")

    if not items:
        print(f"  ⚠ empty (no files found, even after recursive walk)")
        continue

    by_ext: dict[str, list[dict]] = {}
    for it in items:
        ext = Path(it["name"]).suffix.lower()
        by_ext.setdefault(ext, []).append(it)

    folder_size = sum(it["size"] for it in items)
    total_files += len(items)
    total_size += folder_size

    video_count = sum(
        len(v) for ext, v in by_ext.items() if ext in VIDEO_EXTS
    )
    photo_count = sum(
        len(v) for ext, v in by_ext.items() if ext in PHOTO_EXTS
    )
    other_count = len(items) - video_count - photo_count

    print(f"  ✓ {len(items)} files, {fmt_size(folder_size)} total")
    print(f"    videos: {video_count} | photos: {photo_count} | other: {other_count}")

    # Breakdown by extension
    for ext in sorted(by_ext.keys()):
        sub = by_ext[ext]
        avg = sum(it["size"] for it in sub) / max(1, len(sub))
        print(f"      {ext or '(no ext)':12} × {len(sub):3}  avg {fmt_size(avg)}")

    # Sample names
    print(f"    sample names:")
    for it in items[:5]:
        print(f"      - {it['name']}  ({fmt_size(it['size'])})")

    # Red flags
    huge = [it for it in items if it["size"] > 500 * 1024 * 1024]
    if huge:
        print(f"    ⚠ {len(huge)} files >500MB (могут быть 4K-видео, проверь)")

    weird = [
        it for it in items
        if Path(it["name"]).suffix.lower() not in (VIDEO_EXTS | PHOTO_EXTS)
    ]
    if weird:
        print(f"    ⚠ {len(weird)} non-photo/video files: "
              f"{[it['name'] for it in weird[:5]]}")

    all_summaries.append({
        "label": label,
        "files": len(items),
        "size": folder_size,
        "videos": video_count,
        "photos": photo_count,
    })

print("\n" + "=" * 70)
print(f"GRAND TOTAL: {total_files} files, {fmt_size(total_size)}")
print()
total_videos = sum(s["videos"] for s in all_summaries)
total_photos = sum(s["photos"] for s in all_summaries)
print(f"  videos: {total_videos}")
print(f"  photos: {total_photos}")
print()
print("Storage on nox-maksim (CX33, 38 GB disk, ~5 GB used now):")
free_gb = 33
need_gb = total_size / (1024**3)
print(f"  needs ~{need_gb:.1f} GB, free ~{free_gb} GB → "
      f"{'OK ✓' if need_gb < free_gb * 0.5 else '⚠ TIGHT' if need_gb < free_gb * 0.8 else '❌ NO ROOM'}")
