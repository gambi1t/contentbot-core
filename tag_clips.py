"""Vision-based metadata tagger for clips-to-upload/.

For each media file (jpg/mp4/mov), creates a sibling .json with:
  - description (1-2 sentences in Russian)
  - tags (5-10 keywords for search)
  - season, time_of_day, weather (when discernible)
  - has_people, has_maksim (best guess)
  - quality_grade (B-roll usable / event-specific / weak)
  - duration_sec (for videos)
  - resolution (when easily readable)

Uses Claude Sonnet 4.6 vision. For videos — extracts 3 frames via
ffmpeg (1s, middle, end) and sends as 3 images in one message.
Cost ~$0.005-0.01 per file. For ~180 files → ~$1-2.

Resume: if <name>.json already exists, skip.

Usage:
    python tag_clips.py
    python tag_clips.py --only karting     # one category
    python tag_clips.py --dry-run          # plan only
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# UTF-8 stdout on Windows
if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# override=True — system env may have an empty ANTHROPIC_API_KEY which
# blocks .env from kicking in. Common after using anthropic CLI tools
# that export empty defaults.
load_dotenv(override=True)

import anthropic

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY not set in .env or environment")
    sys.exit(2)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

STAGING = Path(__file__).parent / "clips-to-upload"
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png"}

SYSTEM_PROMPT = """Ты — annotator B-roll архива для контент-бота. Тебе \
дают 1-3 кадра одного видео (или одну фотографию). Опиши что на них \
кратко и расставь теги. Контекст — это материалы из бизнеса Максима \
Юмсунова (Тюмень):
- картинг-центр Life Drive (16 лет)
- глэмпинг в сосновом лесу (3 года, 6 A-frame домиков)
- SUP-маршруты на местных водоёмах
- активный отдых на технике
- корпоративы, дни рождения, личная семья (трое детей)

Верни СТРОГО JSON, без markdown-обёрток:
{
  "description": "1-2 предложения по-русски — что на кадрах",
  "tags": ["тег1", "тег2", ...],            // 5-10 ключевых слов
  "scene": "одно слово: glamping|karting|sup|nature|team|personal|food|other",
  "season": "winter|spring|summer|autumn|unknown",
  "time_of_day": "morning|day|evening|night|unknown",
  "weather": "clear|cloudy|rain|snow|unknown",
  "has_people": true|false,
  "has_maksim": "yes|no|maybe",             // мужчина 40 лет, темные волосы
  "quality_grade": "broll|event|weak"       // broll = универсальный, event = специфическое событие, weak = не подходит
}
"""


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def extract_video_frames(video_path: Path, out_dir: Path) -> list[Path]:
    """Extract 3 frames from video using ffmpeg: 1s, middle, end."""
    # Get duration via ffprobe
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(probe.stdout.strip() or 0)
    except Exception:
        duration = 0

    if duration < 0.5:
        # Very short — single frame
        frames_at = [0]
    elif duration < 3:
        frames_at = [duration / 2]
    else:
        # 1s, middle, end-1s
        frames_at = [1.0, duration / 2, max(0.1, duration - 1)]

    frame_paths = []
    for i, t in enumerate(frames_at):
        out = out_dir / f"frame_{i}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
                 "-vframes", "1", "-q:v", "5",  # quality 5 (decent JPEG)
                 "-vf", "scale='min(1024,iw)':-2",  # cap width 1024
                 str(out)],
                capture_output=True, timeout=30,
            )
            if out.exists() and out.stat().st_size > 0:
                frame_paths.append(out)
        except Exception as e:
            print(f"    ffmpeg frame {i} failed: {e}")
    return frame_paths


def encode_image_to_base64(path: Path) -> tuple[str, str]:
    """Returns (base64_data, media_type)."""
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
    elif suffix == ".png":
        media_type = "image/png"
    else:
        media_type = "image/jpeg"
    data = path.read_bytes()
    return base64.standard_b64encode(data).decode("ascii"), media_type


def downscale_for_vision(image_path: Path, max_dim: int = 1568) -> Path:
    """Downscale image to fit Anthropic vision limits (1568 px on long side).
    Returns path to downscaled temp file (or original if already small)."""
    from PIL import Image
    img = Image.open(image_path)
    if max(img.size) <= max_dim:
        return image_path
    img.thumbnail((max_dim, max_dim))
    tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    img.save(tmp, "JPEG", quality=85)
    return tmp


def tag_file(media_path: Path) -> dict | None:
    """Call Claude vision; return parsed JSON or None on failure."""
    suffix = media_path.suffix.lower()
    is_video = suffix in VIDEO_EXTS

    # Prepare image paths for Anthropic
    if is_video:
        with tempfile.TemporaryDirectory() as td:
            frames = extract_video_frames(media_path, Path(td))
            if not frames:
                return {"error": "no frames extracted"}
            content_parts = []
            for fp in frames:
                downscaled = downscale_for_vision(fp)
                b64, media_type = encode_image_to_base64(downscaled)
                content_parts.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                })
            content_parts.append({
                "type": "text",
                "text": f"Это {len(frames)} кадра из одного видео ({media_path.name}). "
                        f"Опиши сцену + теги в JSON по схеме system-промпта.",
            })
            return _run_claude(content_parts, media_path)
    else:
        # Single photo
        downscaled = downscale_for_vision(media_path)
        b64, media_type = encode_image_to_base64(downscaled)
        content_parts = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
            {
                "type": "text",
                "text": f"Это одно фото ({media_path.name}). Опиши + теги в JSON.",
            },
        ]
        return _run_claude(content_parts, media_path)


def _run_claude(content_parts: list, media_path: Path) -> dict | None:
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content_parts}],
        )
        raw = resp.content[0].text if resp.content else ""
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    # Parse JSON tolerantly
    import re
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*\n?", "", s)
    s = re.sub(r"\n?\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        return {"error": f"no JSON in response: {raw[:200]}"}
    try:
        parsed = json.loads(s[i:j + 1])
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse: {e}; raw: {raw[:200]}"}
    parsed["_source"] = {
        "filename": media_path.name,
        "size_bytes": media_path.stat().st_size,
    }
    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="only one category dir")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Collect media files (skip .json sidecars and .heic — already converted)
    all_files: list[Path] = []
    for p in sorted(STAGING.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in (".json",):
            continue
        if p.suffix.lower() == ".heic":
            continue  # converted already; skip
        if "_meta" in p.parts:
            continue
        if args.only and args.only not in p.parts:
            continue
        if p.suffix.lower() in (VIDEO_EXTS | PHOTO_EXTS):
            all_files.append(p)

    print(f"Found {len(all_files)} media files to tag")

    # Skip already-tagged
    pending = []
    skipped = 0
    for p in all_files:
        sidecar = p.with_suffix(p.suffix + ".json")
        if sidecar.exists() and sidecar.stat().st_size > 0:
            skipped += 1
            continue
        pending.append(p)
    print(f"  already tagged: {skipped}")
    print(f"  to tag now:     {len(pending)}")

    if args.dry_run:
        print("(dry-run)")
        for p in pending[:10]:
            print(f"  would tag: {p.relative_to(STAGING)}")
        return

    if not pending:
        print("Nothing to do.")
        return

    stats = {"tagged": 0, "failed": 0}
    for i, p in enumerate(pending, 1):
        rel = p.relative_to(STAGING)
        print(f"  [{i}/{len(pending)}] {rel}", end=" ", flush=True)
        result = tag_file(p)
        if result is None or "error" in result:
            stats["failed"] += 1
            err = (result or {}).get("error", "unknown")
            print(f"✗ {err[:80]}")
            continue
        sidecar = p.with_suffix(p.suffix + ".json")
        sidecar.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        stats["tagged"] += 1
        desc = result.get("description", "")[:60]
        scene = result.get("scene", "?")
        print(f"✓ [{scene}] {desc}")

    print()
    print("=" * 60)
    print(f"  Tagged: {stats['tagged']}")
    print(f"  Failed: {stats['failed']}")


if __name__ == "__main__":
    main()
