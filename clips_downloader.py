"""Download a curated B-roll base set from Maksim's SMM Yandex.Disk links.

Strategy (13 May 2026, MVP):
- 6 public links categorized by SOURCES below.
- Per-category limits + skip-paths to avoid heavyweight content
  (full interview footage 2.9 GB, advertising shoot 1285 jpegs).
- Even-stride sampling — `every-N` from a sorted file list gives variety
  without LLM-vision cost. Quality re-filter (vision-based) is a later
  step on the downloaded sample.
- File size cap 100 MB hard-stops anything that's clearly not a B-roll
  clip (raw interviews, multi-min uncut takes).
- Resume: if local file exists with same size, skip download.

Output: D:/AI/maksim-bot/clips-to-upload/<label>/{photos,videos}/
plus `_meta/download_log.json` with what was taken/skipped and why.

Usage:
    python clips_downloader.py                # download all 6 sources
    python clips_downloader.py --only karting # one category
    python clips_downloader.py --dry-run      # plan only, no download
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import yadisk

# UTF-8 stdout on Windows
if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STAGING = Path(__file__).parent / "clips-to-upload"
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_FILE_SIZE_MB = 100  # hard skip beyond this

# ── Source config — 6 SMM links + per-category curation rules ────────
SOURCES = [
    {
        "label": "sup",
        "public_key": "https://disk.yandex.ru/d/PFPhFvquRA-1yQ",
        "max_videos": 15,
        "max_photos": 13,
        "include_paths": ["/лето 2024"],  # skip "транснефть корпоратив 2024"
        "skip_patterns": [],
    },
    {
        "label": "karting",
        "public_key": "https://disk.yandex.ru/d/B9rvARqs7rQetA",
        "max_videos": 15,
        "max_photos": 50,
        # Из 7 подпапок берём только живые корпоративные кадры
        # (не «Рекламная съёмка» — продакшен-шоты, не B-roll;
        # не «интервью» — финальный продукт; не «детский день
        # рождения Милена» — личное, не нужно)
        "include_paths": [
            "/корпоратив Life Drive 2024",
            "/корпоратив Моторс",
            "/кейтеринг",
        ],
        "skip_patterns": ["рекламная", "интервью"],
    },
    {
        "label": "glamping",
        "public_key": "https://disk.yandex.ru/d/r5P-pPwdGxi1Jw",
        "max_videos": 5,
        "max_photos": 30,
        "include_paths": [],  # top-level + nested all in
        "skip_patterns": [],
    },
    {
        "label": "glamping_holiday",
        "public_key": "https://disk.yandex.ru/d/VJ8t9Sg6fVggPw",
        "max_videos": 15,
        "max_photos": 15,
        "include_paths": [],
        "skip_patterns": [],
    },
    {
        "label": "glamping_evening",
        "public_key": "https://disk.yandex.ru/d/v9-Xb5TcDEmcQg",
        "max_videos": 15,
        "max_photos": 4,
        "include_paths": [],
        "skip_patterns": [],
    },
    {
        "label": "personal",
        "public_key": "https://disk.yandex.ru/d/LaM6WPA-zYqfxA",
        "max_videos": 15,
        "max_photos": 0,
        # «разные» — нейтральное содержимое (без др Милены / премии)
        "include_paths": ["/разные"],
        "skip_patterns": [],
    },
]


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def walk_public_recursive(y: yadisk.YaDisk, public_key: str):
    """Yield (file_dict, rel_dir) recursive."""
    queue = [""]
    visited = set()
    while queue:
        rel = queue.pop(0)
        if rel in visited:
            continue
        visited.add(rel)
        try:
            if rel:
                iterator = y.public_listdir(public_key, path=rel, limit=10000)
            else:
                iterator = y.public_listdir(public_key, limit=10000)
            for item in iterator:
                if item.type == "file":
                    yield {
                        "name": item.name,
                        "path": item.path,
                        "size": item.size or 0,
                        "rel_dir": rel or "/",
                    }
                elif item.type == "dir":
                    sub_rel = f"{rel}/{item.name}" if rel else f"/{item.name}"
                    queue.append(sub_rel)
        except Exception as e:
            print(f"    ⚠ list {rel!r} failed: {e}")


def even_stride_sample(items: list, n: int) -> list:
    """Pick `n` items evenly spaced from `items`. Better than first-N or
    random for getting variety from a sequential phone-camera folder."""
    if len(items) <= n:
        return items
    stride = len(items) / n
    return [items[int(i * stride)] for i in range(n)]


def matches_include(file: dict, include_paths: list[str]) -> bool:
    """File's rel_dir starts with at least one include_path."""
    if not include_paths:
        return True  # no whitelist = allow all
    rel = file["rel_dir"]
    for inc in include_paths:
        if rel == inc or rel.startswith(inc + "/"):
            return True
    return False


def matches_skip(file: dict, skip_patterns: list[str]) -> bool:
    """File path or name contains any skip-pattern (case-insensitive)."""
    lower = (file["rel_dir"] + "/" + file["name"]).lower()
    for skip in skip_patterns:
        if skip.lower() in lower:
            return True
    return False


def plan_category(source: dict, all_files: list[dict]) -> dict:
    """Return {videos: [...], photos: [...], skipped: [...]}."""
    label = source["label"]
    max_v = source["max_videos"]
    max_p = source["max_photos"]
    include = source.get("include_paths", [])
    skip = source.get("skip_patterns", [])
    size_cap = MAX_FILE_SIZE_MB * 1024 * 1024

    eligible_videos = []
    eligible_photos = []
    skipped = []

    for f in all_files:
        ext = Path(f["name"]).suffix.lower()
        reason = None
        if not matches_include(f, include):
            reason = f"not in include_paths {include}"
        elif matches_skip(f, skip):
            reason = f"matched skip pattern"
        elif f["size"] > size_cap:
            reason = f"size {fmt_size(f['size'])} > {MAX_FILE_SIZE_MB} MB cap"
        elif ext in VIDEO_EXTS:
            eligible_videos.append(f)
            continue
        elif ext in PHOTO_EXTS:
            eligible_photos.append(f)
            continue
        else:
            reason = f"unknown ext {ext}"
        skipped.append({**f, "skip_reason": reason})

    # Sort by name for deterministic sampling
    eligible_videos.sort(key=lambda f: f["name"])
    eligible_photos.sort(key=lambda f: f["name"])

    # Even-stride sample down to max
    picked_videos = even_stride_sample(eligible_videos, max_v) if max_v > 0 else []
    picked_photos = even_stride_sample(eligible_photos, max_p) if max_p > 0 else []

    return {
        "label": label,
        "videos_eligible": len(eligible_videos),
        "photos_eligible": len(eligible_photos),
        "videos_picked": picked_videos,
        "photos_picked": picked_photos,
        "skipped_count": len(skipped),
    }


def download_file(y: yadisk.YaDisk, public_key: str, file_path: str,
                  local_path: Path, expected_size: int) -> str:
    """Download single file. Returns 'downloaded', 'skipped_exists',
    or 'failed: <reason>'."""
    if local_path.exists():
        actual = local_path.stat().st_size
        if actual == expected_size:
            return "skipped_exists"
        # Partial / size-mismatch. Try unlink; if file is locked
        # (another process or AV scan), skip and report — don't crash.
        print(f"      size mismatch ({actual} vs {expected_size}), re-download")
        try:
            local_path.unlink()
        except PermissionError as e:
            return f"failed: locked, can't unlink ({e})"
        except OSError as e:
            return f"failed: unlink error ({e})"

    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        y.download_public(
            public_key,
            str(local_path),
            path=file_path.replace(f"/disk/Загрузки/", "").replace(f"disk:/", ""),
        )
        return "downloaded"
    except Exception:
        # download_public with `path=` may fail on certain link structures;
        # retry via the direct-download-link API + urllib.
        try:
            link = y.get_public_download_link(public_key, path=file_path)
            import urllib.request
            urllib.request.urlretrieve(link, str(local_path))
            return "downloaded_via_link"
        except Exception as e2:
            return f"failed: {type(e2).__name__}: {e2}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run only one category by label")
    parser.add_argument("--dry-run", action="store_true",
                        help="show plan but don't download")
    args = parser.parse_args()

    y = yadisk.YaDisk()
    STAGING.mkdir(parents=True, exist_ok=True)
    meta_dir = STAGING / "_meta"
    meta_dir.mkdir(exist_ok=True)

    log = {"sources": []}

    sources_to_run = [s for s in SOURCES if not args.only or s["label"] == args.only]
    if args.only and not sources_to_run:
        print(f"ERROR: no source with label {args.only!r}")
        sys.exit(1)

    grand_videos = 0
    grand_photos = 0
    grand_size = 0
    grand_downloaded = 0
    grand_skipped_existing = 0
    grand_failed = 0

    for source in sources_to_run:
        label = source["label"]
        print(f"\n{'='*70}")
        print(f"[{label}]  {source['public_key']}")
        print(f"{'='*70}")

        # Discover
        all_files = list(walk_public_recursive(y, source["public_key"]))
        print(f"  Discovered: {len(all_files)} files")

        # Plan
        plan = plan_category(source, all_files)
        v_sample = plan["videos_picked"]
        p_sample = plan["photos_picked"]
        print(f"  Eligible:   videos {plan['videos_eligible']} → "
              f"picked {len(v_sample)} | "
              f"photos {plan['photos_eligible']} → picked {len(p_sample)}")
        print(f"  Skipped:    {plan['skipped_count']} files")

        cat_size = sum(f["size"] for f in v_sample + p_sample)
        print(f"  Plan size:  {fmt_size(cat_size)}")
        grand_size += cat_size

        if args.dry_run:
            print(f"  (dry-run, не качаю)")
            log["sources"].append({**plan, "downloaded": 0, "dry_run": True})
            continue

        # Download
        cat_downloaded = 0
        cat_skipped_existing = 0
        cat_failed = 0

        for kind, files, subdir in [
            ("video", v_sample, "videos"),
            ("photo", p_sample, "photos"),
        ]:
            target_dir = STAGING / label / subdir
            for i, f in enumerate(files, 1):
                # yadisk wants `path` as relative path inside the public
                # resource. `rel_dir` is either "/" (root) or "/sub/sub".
                rel_dir = f["rel_dir"].rstrip("/")
                rel_path = f"{rel_dir}/{f['name']}" if rel_dir else f"/{f['name']}"
                # Prevent collisions: if two source subfolders have files
                # with the same name (e.g. karting has IMG_1218.heic in
                # multiple subdirs after photoshoots reused phone), they'd
                # overwrite each other in the flat target. Prefix with a
                # short stable hash of the rel_dir to keep filenames
                # unique. Files from the public-resource root keep their
                # original name (no prefix needed).
                if rel_dir:
                    import hashlib
                    sub_tag = hashlib.md5(rel_dir.encode("utf-8")).hexdigest()[:6]
                    local_name = f"{sub_tag}__{f['name']}"
                else:
                    local_name = f["name"]
                local = target_dir / local_name
                print(f"  [{kind} {i}/{len(files)}] {f['name']} "
                      f"({fmt_size(f['size'])})", end=" ", flush=True)
                result = download_file(
                    y, source["public_key"], rel_path, local, f["size"],
                )
                print(result, flush=True)
                if result == "downloaded" or result == "downloaded_via_link":
                    cat_downloaded += 1
                elif result == "skipped_exists":
                    cat_skipped_existing += 1
                else:
                    cat_failed += 1

        print(f"\n  Category result: ✓{cat_downloaded} downloaded, "
              f"⤵{cat_skipped_existing} already had, ✗{cat_failed} failed")
        grand_downloaded += cat_downloaded
        grand_skipped_existing += cat_skipped_existing
        grand_failed += cat_failed

        log["sources"].append({
            **plan,
            "downloaded": cat_downloaded,
            "skipped_existing": cat_skipped_existing,
            "failed": cat_failed,
        })

        grand_videos += len(v_sample)
        grand_photos += len(p_sample)

    print(f"\n{'='*70}")
    print(f"GRAND TOTAL")
    print(f"{'='*70}")
    print(f"  Plan: {grand_videos} videos + {grand_photos} photos "
          f"= {grand_videos + grand_photos} files, ~{fmt_size(grand_size)}")
    if not args.dry_run:
        print(f"  Real: ✓{grand_downloaded} downloaded, "
              f"⤵{grand_skipped_existing} already had, "
              f"✗{grand_failed} failed")

    (meta_dir / "download_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n  Log: {meta_dir / 'download_log.json'}")


if __name__ == "__main__":
    main()
