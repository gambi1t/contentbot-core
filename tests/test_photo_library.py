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
    """_ken_burns_filter for zoom_in_shoes: мягкий зум + горизонтальный дрейф.

    7 июня 2026 (фидбэк Артёма): статичный центр-зум 1.0→1.15 был «тупым
    увеличением» и резал обувь. Заменён на мягкий зум 1.0→1.06 + лёгкий
    горизонтальный pan (живое диагональное движение). По вертикали окно
    центрировано — вертикальную зону обуви выбирает split-crop (anchor)
    в _assemble_pro.
    """
    print("\n-- zoom_in_shoes Ken Burns filter --")
    from video_assembler import _ken_burns_filter

    f = _ken_burns_filter(duration_frames=84, variant="zoom_in_shoes")  # 2.8s × 30fps
    # Basic structure
    _assert("scale=4320:7680" in f, "base scale canvas present", errors)
    _assert("zoompan" in f, "zoompan stage present", errors)
    # Y центрирован — split-crop рулит вертикальной зоной обуви
    _assert("ih/2-(ih/zoom/2)" in f, "y centred (split-crop handles vertical)", errors)
    # X с горизонтальным дрейфом (pan) для «живости», не статичный центр
    _assert("on/" in f, "x has horizontal pan drift (lively diagonal)", errors)
    _assert("(iw-iw/zoom)" in f, "x pan uses window-width math", errors)
    # Мягкий зум 1.06 (вдвое мягче старого 1.15) — обувь целиком, не режется
    _assert("1.06" in f, "gentle zoom ceiling 1.06 (was 1.15, too aggressive)", errors)

    # Sanity: default variants still work
    f_default = _ken_burns_filter(duration_frames=84, variant="zoom_in")
    _assert("ih/2-(ih/zoom/2)" in f_default, "default zoom_in still centers y", errors)
    _assert("zoom_in_shoes" not in f_default, "no cross-contamination", errors)


def test_split_anchor_keeps_shoe_in_frame(errors: list[str]) -> None:
    """Геометрия split-anchor: обувь на lifestyle-фото лежит в нижних
    [0.60, 0.95] высоты. Anchor должен держать её ЦЕЛИКОМ в split-слоте на
    всех кадрах Ken Burns (zoom 1.0→1.06).

    7 июня 2026: 0.75 и 0.62 геометрически срезали обувь снизу (фидбэк Артёма
    «обувь уезжает/режется»). Дефолт поднят до SHOES_SPLIT_ANCHOR (низ фото).
    """
    print("\n-- split anchor keeps shoe in frame --")
    from video_assembler import (
        _split_visible_photo_band,
        _shoe_anchor_keeps_shoe_visible,
        SHOES_SPLIT_ANCHOR,
    )

    SHOE_TOP, SHOE_BOT = 0.60, 0.95  # типичная lifestyle-обувь

    # Старые значения СРЕЗАЛИ обувь (баг, который ловил Артём)
    _assert(
        not _shoe_anchor_keeps_shoe_visible(SHOE_TOP, SHOE_BOT, 0.75),
        "anchor 0.75 срезает обувь снизу (старый баг)", errors,
    )
    _assert(
        not _shoe_anchor_keeps_shoe_visible(SHOE_TOP, SHOE_BOT, 0.62),
        "anchor 0.62 срезает обувь ещё сильнее (моя ошибка)", errors,
    )

    # Новый дефолт держит обувь ЦЕЛИКОМ на старте (zoom=1.0) и конце (1.06)
    _assert(
        _shoe_anchor_keeps_shoe_visible(SHOE_TOP, SHOE_BOT, SHOES_SPLIT_ANCHOR),
        f"anchor {SHOES_SPLIT_ANCHOR} держит обувь целиком на всех кадрах", errors,
    )

    # Обувь приподнята, не упирается в самый низ слота (запас снизу)
    bt, bb = _split_visible_photo_band(SHOES_SPLIT_ANCHOR, 1.0)
    shoe_bot_in_slot = (SHOE_BOT - bt) / (bb - bt)
    _assert(
        shoe_bot_in_slot <= 0.98,
        f"низ обуви не у самого края слота (={shoe_bot_in_slot:.2f})", errors,
    )

    # Меньший anchor показывает БОЛЕЕ ВЕРХНЮЮ зону фото (доказательство, что
    # 0.62 двигало «не туда» — к ногам, а не к обуви)
    top_low, _ = _split_visible_photo_band(0.62, 1.0)
    top_high, _ = _split_visible_photo_band(1.0, 1.0)
    _assert(
        top_low < top_high,
        "меньший anchor = более верхняя зона фото (ноги, не обувь)", errors,
    )


def test_project_broll_inventory(errors: list[str]) -> None:
    """_project_broll_inventory: видео (broll_*.mp4) + фото (photos/*) проекта.

    10 июня 2026: кнопка «Управление B-roll» в карточке показывалась только
    при наличии broll_*.mp4 — проект с 8 фото (ready_*.jpg в photos/) кнопки
    не имел, удалять фото можно было только по ssh. Хелпер — единый источник
    инвентаря для кнопки и broll_manage.
    """
    print("\n-- project broll inventory --")
    tmp = Path(tempfile.mkdtemp(prefix="proj_inv_test_"))
    try:
        # (а) проект Артёма: 0 видео + 3 фото → видео пуст, фото найдены
        (tmp / "photos").mkdir()
        for n in ("ready_01.jpg", "ready_02.png", "ready_03.jpg"):
            _make_png(tmp / "photos" / n)
        videos, photos = bot._project_broll_inventory(tmp)
        _assert(videos == [], "0 mp4 → видео пуст", errors)
        _assert(len(photos) == 3, f"3 фото найдены (got {len(photos)})", errors)
        _assert([p.name for p in photos] == ["ready_01.jpg", "ready_02.png", "ready_03.jpg"],
                "фото отсортированы", errors)

        # (б) не-картинки в photos/ игнорируются
        (tmp / "photos" / "meta.json").write_text("{}")
        (tmp / "photos" / "note.txt").write_text("x")
        _, photos = bot._project_broll_inventory(tmp)
        _assert(len(photos) == 3, "json/txt не считаются фото", errors)

        # (в) видео тоже находятся
        (tmp / "broll_01.mp4").write_bytes(b"fake")
        videos, _ = bot._project_broll_inventory(tmp)
        _assert(len(videos) == 1, "broll_*.mp4 найден", errors)

        # (г) пустой проект → оба пусты
        empty = Path(tempfile.mkdtemp(prefix="proj_inv_empty_"))
        try:
            videos, photos = bot._project_broll_inventory(empty)
            _assert(videos == [] and photos == [], "пустой проект → пусто", errors)
        finally:
            shutil.rmtree(empty, ignore_errors=True)

        # (д) защита резолва файла на удаление: traversal запрещён
        _assert(bot._safe_project_file(tmp, "photos/ready_01.jpg") is not None,
                "photos/ready_01.jpg резолвится", errors)
        _assert(bot._safe_project_file(tmp, "../../etc/passwd") is None,
                "traversal .. отклонён", errors)
        _assert(bot._safe_project_file(tmp, "broll_01.mp4") is not None,
                "видео в корне резолвится", errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pip_overlay_position(errors: list[str]) -> None:
    """Геометрия круглого PiP (floating avatar): позиция overlay в углу.

    7 июня 2026 (запрос Артёма): talking-head аватар круглым PiP в углу
    поверх fullscreen B-roll. Выбор Артёма — снизу-справа (br).
    """
    print("\n-- pip overlay position --")
    from video_assembler import _pip_overlay_position, CANVAS_W, CANVAS_H

    D, M = 320, 48

    # Снизу-справа (выбор Артёма)
    x, y = _pip_overlay_position(CANVAS_W, CANVAS_H, D, M, "br")
    _assert(x == CANVAS_W - D - M, f"br: x = W-D-M (got {x})", errors)
    _assert(y == CANVAS_H - D - M, f"br: y = H-D-M (got {y})", errors)
    # Кружок целиком в пределах кадра
    _assert(0 <= x and x + D <= CANVAS_W, "br: в пределах по X", errors)
    _assert(0 <= y and y + D <= CANVAS_H, "br: в пределах по Y", errors)

    # Остальные углы
    xbl, ybl = _pip_overlay_position(CANVAS_W, CANVAS_H, D, M, "bl")
    _assert(xbl == M and ybl == CANVAS_H - D - M, "bl корректно", errors)
    xtr, ytr = _pip_overlay_position(CANVAS_W, CANVAS_H, D, M, "tr")
    _assert(xtr == CANVAS_W - D - M and ytr == M, "tr корректно", errors)
    xtl, ytl = _pip_overlay_position(CANVAS_W, CANVAS_H, D, M, "tl")
    _assert(xtl == M and ytl == M, "tl корректно", errors)


def test_plan_floating_montage(errors: list[str]) -> None:
    """План floating-монтажа: intro avatar_full → body pip → outro avatar_full.

    7 июня 2026: новый формат «плавающий аватар». Аватар на весь экран в
    начале (hook) и конце (CTA), в середине — B-roll fullscreen + круглый
    аватар-PiP (layout 'pip').
    """
    print("\n-- plan floating montage --")
    from video_assembler import _plan_floating_montage

    photos = [Path(f"/x/p{i}.mp4") for i in range(5)]
    plan = _plan_floating_montage(
        [], photos, photo_clip_dur=2.8, avatar_duration=30.0,
        intro_dur=5.0, outro_dur=4.0,
    )

    # Первый сегмент — intro avatar_full
    _assert(plan[0]["layout"] == "avatar_full" and plan[0]["start"] == 0.0,
            "intro = avatar_full с 0.0", errors)
    _assert(abs(plan[0]["end"] - 5.0) < 0.01, "intro длится intro_dur", errors)
    # Последний — outro avatar_full (CTA)
    _assert(plan[-1]["layout"] == "avatar_full", "outro = avatar_full", errors)
    # Середина — pip сегменты с broll_index
    mids = [s for s in plan if s["layout"] == "pip"]
    _assert(len(mids) > 0, f"есть pip-сегменты (got {len(mids)})", errors)
    _assert(all(s["broll_index"] is not None for s in mids),
            "все pip имеют broll_index", errors)
    # План покрывает avatar_duration без хвоста
    _assert(abs(plan[-1]["end"] - 30.0) < 0.01,
            f"план до avatar_duration (got {plan[-1]['end']})", errors)
    # Сегменты непрерывны (без дыр/нахлёстов)
    for a, b in zip(plan, plan[1:]):
        _assert(abs(a["end"] - b["start"]) < 0.001,
                f"непрерывность {a['end']}=={b['start']}", errors)
    # Пустой вход → ошибка
    try:
        _plan_floating_montage([], [], 2.8, 30.0)
        _assert(False, "пустой вход должен бросить AssemblyError", errors)
    except Exception:
        _assert(True, "пустой вход → ошибка", errors)


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
    test_split_anchor_keeps_shoe_in_frame(errors)
    test_project_broll_inventory(errors)
    test_pip_overlay_position(errors)
    test_plan_floating_montage(errors)
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
