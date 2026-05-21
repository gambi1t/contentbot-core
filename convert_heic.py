"""HEIC → JPG conversion for the clips-to-upload archive.

iPhone photos come as `.heic` (HEIF format). Telegram bot APIs, Notion,
and most ffmpeg pipelines handle `.jpg` more reliably. Convert all
`.heic` files in clips-to-upload/* to JPG quality 90, preserve EXIF.

Behavior:
- Scans clips-to-upload/**/*.heic
- For each: creates <name>.jpg next to it
- Deletes original .heic after successful conversion
- Resume-safe: if .jpg already exists with non-zero size, skip
- Reports total saved / fails / unchanged

Usage:
    python convert_heic.py
    python convert_heic.py --keep-original   # don't delete .heic
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image
import pillow_heif

# UTF-8 stdout on Windows
if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

pillow_heif.register_heif_opener()

STAGING = Path(__file__).parent / "clips-to-upload"


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def convert_one(heic_path: Path, keep_original: bool) -> dict:
    """Convert one .heic → .jpg. Returns dict with result."""
    jpg_path = heic_path.with_suffix(".jpg")
    if jpg_path.exists() and jpg_path.stat().st_size > 0:
        return {"status": "skipped_exists", "path": str(heic_path)}
    try:
        img = Image.open(heic_path)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        # Preserve EXIF if available
        exif = img.info.get("exif")
        save_kwargs = {"quality": 90, "optimize": True}
        if exif:
            save_kwargs["exif"] = exif
        img.save(jpg_path, "JPEG", **save_kwargs)
    except Exception as e:
        return {"status": "failed", "path": str(heic_path),
                "error": f"{type(e).__name__}: {e}"}
    heic_size = heic_path.stat().st_size
    jpg_size = jpg_path.stat().st_size
    if not keep_original:
        try:
            heic_path.unlink()
        except Exception as e:
            return {"status": "converted_but_keep", "path": str(heic_path),
                    "warning": f"can't unlink: {e}",
                    "heic_size": heic_size, "jpg_size": jpg_size}
    return {"status": "converted", "path": str(heic_path),
            "heic_size": heic_size, "jpg_size": jpg_size}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-original", action="store_true",
                        help="don't delete .heic after conversion")
    args = parser.parse_args()

    heic_files = sorted(STAGING.rglob("*.heic")) + sorted(STAGING.rglob("*.HEIC"))
    if not heic_files:
        print("No .heic files found.")
        return

    print(f"Found {len(heic_files)} .heic files")
    print()

    stats = {"converted": 0, "skipped_exists": 0, "failed": 0,
             "total_heic_size": 0, "total_jpg_size": 0}
    failures = []

    for i, hp in enumerate(heic_files, 1):
        result = convert_one(hp, args.keep_original)
        status = result["status"]
        rel = hp.relative_to(STAGING)
        if status == "converted":
            stats["converted"] += 1
            stats["total_heic_size"] += result["heic_size"]
            stats["total_jpg_size"] += result["jpg_size"]
            print(f"  [{i}/{len(heic_files)}] ✓ {rel}  "
                  f"{fmt_size(result['heic_size'])} → {fmt_size(result['jpg_size'])}")
        elif status == "skipped_exists":
            stats["skipped_exists"] += 1
            print(f"  [{i}/{len(heic_files)}] ⤵ {rel}  (jpg already exists)")
        elif status == "converted_but_keep":
            stats["converted"] += 1
            print(f"  [{i}/{len(heic_files)}] ⚠ {rel}  converted but couldn't delete heic: "
                  f"{result.get('warning', '')}")
        else:
            stats["failed"] += 1
            failures.append(result)
            print(f"  [{i}/{len(heic_files)}] ✗ {rel}  {result.get('error', '?')}")

    print()
    print("=" * 60)
    print(f"  Converted:     {stats['converted']}")
    print(f"  Skipped:       {stats['skipped_exists']}")
    print(f"  Failed:        {stats['failed']}")
    if stats["total_heic_size"]:
        print(f"  Size before:   {fmt_size(stats['total_heic_size'])}")
        print(f"  Size after:    {fmt_size(stats['total_jpg_size'])}")
        ratio = stats['total_jpg_size'] / stats['total_heic_size']
        print(f"  Compression:   {ratio:.0%} of original")
    if failures:
        print()
        print("Failures:")
        for f in failures[:10]:
            print(f"  - {f['path']}: {f.get('error')}")

    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
