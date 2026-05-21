"""Tests for the photo library helpers in bot.py and video_assembler.py.

Covers:
- _list_photo_library() in bot.py — used by the B-roll menu to count & preview
- _find_photo_library() in video_assembler — used by the assembler fallback
- _list_library_photos() in video_assembler — used by the assembler fallback

We point PHOTO_LIBRARY_DIR at a temporary directory for each test so we
don't depend on whatever's on disk.

Run: python tests/test_photo_library.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _make_png(path: Path) -> None:
    """Write a minimal valid PNG (1x1 red pixel) to path."""
    # Shortest valid PNG: signature + IHDR + IDAT + IEND
    data = bytes.fromhex(
        "89504e470d0a1a0a"                          # PNG signature
        "0000000d49484452"                          # IHDR length + type
        "00000001000000010806000000"                # 1x1 RGBA
        "1f15c4890000000d49444154"                  # IHDR CRC + IDAT len + type
        "789c626001000000ffff03000006000557bfabd4"  # IDAT data + CRC
        "0000000049454e44ae426082"                  # IEND
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    if not cond:
        errors.append(f"FAIL {msg}")
        print(f"  FAIL {msg}")
    else:
        print(f"  OK {msg}")


def with_temp_library(func):
    """Decorator: swap bot.PHOTO_LIBRARY_DIR to a fresh tempdir for the test."""
    def wrapper(errors):
        tmp = Path(tempfile.mkdtemp(prefix="photolib_test_"))
        original = bot.PHOTO_LIBRARY_DIR
        bot.PHOTO_LIBRARY_DIR = tmp
        try:
            func(tmp, errors)
        finally:
            bot.PHOTO_LIBRARY_DIR = original
            shutil.rmtree(tmp, ignore_errors=True)
    return wrapper


@with_temp_library
def test_missing_directory(tmp: Path, errors: list[str]) -> None:
    print("\n-- missing directory --")
    shutil.rmtree(tmp, ignore_errors=True)  # simulate non-existent path
    result = bot._list_photo_library()
    _assert(result == [], "no directory → empty list", errors)


@with_temp_library
def test_empty_directory(tmp: Path, errors: list[str]) -> None:
    print("\n-- empty directory --")
    result = bot._list_photo_library()
    _assert(result == [], "empty dir → empty list", errors)


@with_temp_library
def test_picks_supported_extensions(tmp: Path, errors: list[str]) -> None:
    print("\n-- picks supported extensions --")
    _make_png(tmp / "midjourney" / "a.png")
    _make_png(tmp / "midjourney" / "b.jpg")
    _make_png(tmp / "midjourney" / "c.jpeg")
    _make_png(tmp / "midjourney" / "d.webp")
    result = bot._list_photo_library()
    _assert(len(result) == 4, f"all 4 image types picked (got {len(result)})", errors)


@with_temp_library
def test_ignores_non_images(tmp: Path, errors: list[str]) -> None:
    print("\n-- ignores non-image files --")
    _make_png(tmp / "a.png")
    (tmp / "readme.txt").write_text("not an image")
    (tmp / "script.py").write_text("# nope")
    result = bot._list_photo_library()
    _assert(len(result) == 1, f"only the png picked (got {len(result)})", errors)
    _assert(result[0].name == "a.png", "correct file returned", errors)


@with_temp_library
def test_recursive_subfolders(tmp: Path, errors: list[str]) -> None:
    print("\n-- recursive subfolders --")
    _make_png(tmp / "midjourney" / "a.png")
    _make_png(tmp / "midjourney" / "nested" / "b.png")
    _make_png(tmp / "other" / "c.png")
    _make_png(tmp / "loose.png")
    result = bot._list_photo_library()
    _assert(len(result) == 4, f"finds all 4 files recursively (got {len(result)})", errors)


@with_temp_library
def test_sorted_output(tmp: Path, errors: list[str]) -> None:
    print("\n-- sorted output for deterministic previews --")
    _make_png(tmp / "z.png")
    _make_png(tmp / "a.png")
    _make_png(tmp / "m.png")
    result = bot._list_photo_library()
    names = [p.name for p in result]
    _assert(names == sorted(names), f"sorted ({names})", errors)


def test_video_assembler_helpers(errors: list[str]) -> None:
    """The assembler has its own photo-lib helpers — smoke-check they still work."""
    print("\n-- video_assembler photo helpers --")
    from video_assembler import _find_photo_library, _list_library_photos

    tmp = Path(tempfile.mkdtemp(prefix="asm_photolib_"))
    try:
        _make_png(tmp / "a.png")
        _make_png(tmp / "sub" / "b.jpg")

        # _list_library_photos is public-ish and takes a dir
        photos = _list_library_photos(tmp)
        _assert(len(photos) == 2, f"_list_library_photos finds 2 files (got {len(photos)})", errors)

        # _find_photo_library is hard-coded to broll-library/photos — just
        # verify it returns Path or None without crashing.
        result = _find_photo_library()
        _assert(
            result is None or isinstance(result, Path),
            "_find_photo_library returns Path or None",
            errors,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_project_photos(errors: list[str]) -> None:
    """_find_project_photos: reads project_dir/photos/ + project_dir/photo_*.*"""
    print("\n-- _find_project_photos --")
    from video_assembler import _find_project_photos

    tmp = Path(tempfile.mkdtemp(prefix="proj_photos_"))
    try:
        # Empty project → empty list
        result = _find_project_photos(tmp)
        _assert(result == [], "empty project → empty list", errors)

        # photos/ subfolder
        _make_png(tmp / "photos" / "a.png")
        _make_png(tmp / "photos" / "b.jpg")
        result = _find_project_photos(tmp)
        _assert(len(result) == 2, f"finds 2 photos in photos/ (got {len(result)})", errors)

        # Loose photo_*.* in project root
        _make_png(tmp / "photo_01.png")
        _make_png(tmp / "photo_02.jpg")
        # Non-matching files should NOT be picked up from root
        _make_png(tmp / "cover.png")  # not photo_*
        (tmp / "readme.txt").write_text("nope")
        result = _find_project_photos(tmp)
        _assert(len(result) == 4, f"finds 2 in photos/ + 2 photo_*.* in root (got {len(result)})", errors)

        # Sorted output (by full path, not just name — paths in different
        # subfolders interleave when sorted lexicographically, which is OK)
        paths = [str(p) for p in result]
        _assert(paths == sorted(paths), f"sorted by path ({[p.name for p in result]})", errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_ken_burns_clips_empty_input(errors: list[str]) -> None:
    """_build_ken_burns_clips: with empty photo list, returns empty list (no ffmpeg call)."""
    print("\n-- _build_ken_burns_clips empty input --")
    from video_assembler import _build_ken_burns_clips

    tmp = Path(tempfile.mkdtemp(prefix="kenburns_"))
    try:
        result = _build_ken_burns_clips([], tmp, clip_duration=2.0)
        _assert(result == [], "empty input → empty list (no ffmpeg)", errors)
        _assert(isinstance(result, list), "returns a list", errors)
        # Custom variants arg
        result2 = _build_ken_burns_clips([], tmp, 2.0, variants=["zoom_in_shoes"])
        _assert(result2 == [], "empty input with custom variants → empty", errors)
        result3 = _build_ken_burns_clips([], tmp, 2.0, variants=[])
        _assert(result3 == [], "empty variants falls back without crash", errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_zoom_in_shoes_filter(errors: list[str]) -> None:
    """_ken_burns_filter for zoom_in_shoes: gentle centre-anchored zoom.

    Bottom anchoring now happens in _assemble_pro's split preparation,
    not in Ken Burns itself. Ken Burns outputs a clean 1080×1920 clip
    with a 1.0→1.15 breathing zoom; _assemble_pro then crops its bottom
    half for shoe-brand photos.
    """
    print("\n-- zoom_in_shoes Ken Burns filter --")
    from video_assembler import _ken_burns_filter

    f = _ken_burns_filter(duration_frames=84, variant="zoom_in_shoes")  # 2.8s × 30fps
    # Basic structure
    _assert("scale=4320:7680" in f, "base scale canvas present", errors)
    _assert("zoompan" in f, "zoompan stage present", errors)
    # Centred on both axes — no bottom anchor in Ken Burns anymore
    _assert("ih/2-(ih/zoom/2)" in f, "y centred (not bottom-anchored)", errors)
    _assert("iw/2-(iw/zoom/2)" in f, "x centred", errors)
    # Gentle breathing zoom, not the aggressive 1.8-2.0 range
    _assert("1.15" in f, "zoom ceiling at 1.15 (gentle)", errors)

    # Sanity: default variants still work
    f_default = _ken_burns_filter(duration_frames=84, variant="zoom_in")
    _assert("ih/2-(ih/zoom/2)" in f_default, "default zoom_in still centers y", errors)
    _assert("zoom_in_shoes" not in f_default, "no cross-contamination", errors)


def test_plan_smart_mixed_montage(errors: list[str]) -> None:
    """_plan_smart_mixed_montage: builds deterministic plan, never cuts mid-clip."""
    print("\n-- _plan_smart_mixed_montage --")
    import video_assembler as va

    # Mock _probe_duration — we don't want to call ffprobe on fake paths.
    original_probe = va._probe_duration
    fake_durations = {"v0.mp4": 4.0, "v1.mp4": 5.5}
    va._probe_duration = lambda p: fake_durations[Path(p).name]

    try:
        # Case 1: 2 videos + 6 photos into a 30s avatar (should fit with slack)
        videos = [Path("v0.mp4"), Path("v1.mp4")]
        photos = [Path(f"p{i}.mp4") for i in range(6)]
        plan = va._plan_smart_mixed_montage(
            videos, photos, photo_clip_dur=2.8, avatar_duration=30.0,
            intro_dur=1.5, outro_dur=1.5,
        )
        _assert(plan[0]["layout"] == "avatar_full", "intro is avatar_full", errors)
        _assert(plan[-1]["layout"] == "avatar_full", "outro is avatar_full", errors)
        _assert(
            abs(plan[-1]["end"] - 30.0) < 0.01,
            f"total duration snaps to avatar ({plan[-1]['end']})",
            errors,
        )
        # Check no gaps / overlaps
        for i in range(len(plan) - 1):
            _assert(
                abs(plan[i]["end"] - plan[i + 1]["start"]) < 0.001,
                f"segment {i} end == segment {i+1} start",
                errors,
            )
        # Video segments = broll_full with full clip duration
        video_segs = [s for s in plan if s["layout"] == "broll_full"]
        _assert(len(video_segs) == 2, f"2 broll_full segments (got {len(video_segs)})", errors)
        for s in video_segs:
            dur = s["end"] - s["start"]
            expected = fake_durations[f"v{s['broll_index']}.mp4"]
            _assert(
                abs(dur - expected) < 0.01,
                f"video #{s['broll_index']} segment dur={dur:.2f} == clip dur={expected}",
                errors,
            )
        # Photo segments = split, each exactly 2.8s (not cut)
        photo_segs = [s for s in plan if s["layout"] == "split"]
        _assert(len(photo_segs) == 6, f"6 split segments (got {len(photo_segs)})", errors)
        for s in photo_segs:
            dur = s["end"] - s["start"]
            _assert(abs(dur - 2.8) < 0.01, f"photo segment dur={dur:.2f} == 2.8", errors)

        # Case 2: overflow — drop clips, don't cut
        plan2 = va._plan_smart_mixed_montage(
            videos, photos, photo_clip_dur=2.8, avatar_duration=10.0,
            intro_dur=1.5, outro_dur=1.5,
        )
        # Active window = 7s. Can fit v0 (4s) + 1 photo (2.8s) = 6.8s. Rest dropped.
        body_segs = [s for s in plan2 if s["broll_index"] is not None]
        body_total = sum(s["end"] - s["start"] for s in body_segs)
        _assert(body_total <= 7.0 + 0.01, f"body fits in 7s window (got {body_total:.2f})", errors)
        # Every segment duration matches its clip's natural duration (no cuts)
        for s in body_segs:
            dur = s["end"] - s["start"]
            if s["layout"] == "broll_full":
                expected = fake_durations[f"v{s['broll_index']}.mp4"]
            else:
                expected = 2.8
            _assert(
                abs(dur - expected) < 0.01,
                f"overflow: clip #{s['broll_index']} not cut mid-play ({dur:.2f})",
                errors,
            )

        # Case 3: photo-only (no videos)
        plan3 = va._plan_smart_mixed_montage(
            [], photos[:3], photo_clip_dur=2.8, avatar_duration=15.0,
        )
        layouts = [s["layout"] for s in plan3[1:-1]]  # body only
        _assert(
            all(l == "split" for l in layouts),
            f"photo-only → all body segments are split ({layouts})",
            errors,
        )
    finally:
        va._probe_duration = original_probe


def main() -> int:
    print("=" * 60)
    print("photo library tests")
    print("=" * 60)

    errors: list[str] = []

    test_missing_directory(errors)
    test_empty_directory(errors)
    test_picks_supported_extensions(errors)
    test_ignores_non_images(errors)
    test_recursive_subfolders(errors)
    test_sorted_output(errors)
    test_video_assembler_helpers(errors)
    test_find_project_photos(errors)
    test_build_ken_burns_clips_empty_input(errors)
    test_zoom_in_shoes_filter(errors)
    test_plan_smart_mixed_montage(errors)

    print("\n" + "=" * 60)
    if errors:
        print(f"Found {len(errors)} failure(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK all photo library tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
