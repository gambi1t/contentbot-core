"""TDD for selfie.broll_picker — pure helpers for the B-roll selection step
in Pipeline 2 (selfie + B-roll mix via assemble_auto_montage).

Contract:
  - BrollItem(kind: Literal["video","image"], source: Path, label: str | None)
  - MAX_BROLL_ITEMS — hard cap per video (7).
  - prepare_broll_in_project(items, project_dir) → None
        copies videos as broll_NNN.mp4 (1-based, zero-padded 3) and
        images as photos/photo_NNN.jpg (preserving extension) so that
        video_assembler.assemble_auto_montage(layout='smart') picks them up.
  - place_selfie_as_avatar(subtitled_path, project_dir) → Path
        copies subtitled.mp4 → avatar_selfie.mp4 (so _find_avatar() in
        video_assembler returns it).
  - validate_added(current, item) → str | None
        returns error message if can't add (over limit), None if OK.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from selfie.broll_picker import (
    BrollItem,
    MAX_BROLL_ITEMS,
    place_selfie_as_avatar,
    prepare_broll_in_project,
    validate_added,
)


# ── BrollItem model ─────────────────────────────────────────────────────────

def test_brollitem_video_kind():
    item = BrollItem(kind="video", source=Path("/tmp/clip.mp4"))
    assert item.kind == "video"
    assert item.source == Path("/tmp/clip.mp4")


def test_brollitem_image_kind():
    item = BrollItem(kind="image", source=Path("/tmp/pic.jpg"))
    assert item.kind == "image"


def test_brollitem_rejects_other_kinds():
    with pytest.raises((ValueError, TypeError, AssertionError)):
        BrollItem(kind="audio", source=Path("/tmp/x"))


def test_brollitem_label_optional():
    item = BrollItem(kind="image", source=Path("/tmp/p.jpg"))
    assert item.label is None
    item2 = BrollItem(kind="image", source=Path("/tmp/p.jpg"), label="glamping_1")
    assert item2.label == "glamping_1"


# ── MAX_BROLL_ITEMS + validate_added ────────────────────────────────────────

def test_max_broll_items_constant_is_7():
    """Max должен быть 7 — это договорённость 8 июня для роликов Максима."""
    assert MAX_BROLL_ITEMS == 7


def test_validate_added_under_limit_returns_none():
    current = [BrollItem(kind="image", source=Path(f"/tmp/{i}.jpg")) for i in range(3)]
    new = BrollItem(kind="image", source=Path("/tmp/new.jpg"))
    assert validate_added(current, new) is None


def test_validate_added_at_limit_returns_error():
    current = [BrollItem(kind="image", source=Path(f"/tmp/{i}.jpg")) for i in range(MAX_BROLL_ITEMS)]
    new = BrollItem(kind="image", source=Path("/tmp/over.jpg"))
    err = validate_added(current, new)
    assert err is not None
    assert "лимит" in err.lower() or "максимум" in err.lower() or str(MAX_BROLL_ITEMS) in err


def test_validate_added_empty_list_returns_none():
    new = BrollItem(kind="video", source=Path("/tmp/c.mp4"))
    assert validate_added([], new) is None


# ── prepare_broll_in_project ────────────────────────────────────────────────

def _make_file(p: Path, content: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def test_prepare_broll_copies_videos_as_numbered(tmp_path):
    src_dir = tmp_path / "lib"
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()

    v1 = src_dir / "clip_a.mp4"
    v2 = src_dir / "clip_b.mp4"
    _make_file(v1, b"video-A")
    _make_file(v2, b"video-B")

    items = [
        BrollItem(kind="video", source=v1),
        BrollItem(kind="video", source=v2),
    ]
    prepare_broll_in_project(items, proj_dir)

    assert (proj_dir / "broll_001.mp4").exists()
    assert (proj_dir / "broll_002.mp4").exists()
    assert (proj_dir / "broll_001.mp4").read_bytes() == b"video-A"
    assert (proj_dir / "broll_002.mp4").read_bytes() == b"video-B"


def test_prepare_broll_copies_images_to_photos_subdir(tmp_path):
    src = tmp_path / "lib" / "pic.jpg"
    _make_file(src, b"image-A")
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()

    items = [BrollItem(kind="image", source=src)]
    prepare_broll_in_project(items, proj_dir)

    photos_dir = proj_dir / "photos"
    assert photos_dir.exists()
    # Один файл photo_001.<ext>
    photos = list(photos_dir.iterdir())
    assert len(photos) == 1
    assert photos[0].name.startswith("photo_001")
    assert photos[0].read_bytes() == b"image-A"


def test_prepare_broll_preserves_image_extension(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    png_src = tmp_path / "x.png"
    webp_src = tmp_path / "y.webp"
    _make_file(png_src, b"png")
    _make_file(webp_src, b"webp")
    items = [
        BrollItem(kind="image", source=png_src),
        BrollItem(kind="image", source=webp_src),
    ]
    prepare_broll_in_project(items, proj)
    photos = sorted((proj / "photos").iterdir())
    assert any(p.suffix == ".png" for p in photos)
    assert any(p.suffix == ".webp" for p in photos)


def test_prepare_broll_mixed_videos_and_images(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    v = tmp_path / "v.mp4"
    i1 = tmp_path / "i1.jpg"
    i2 = tmp_path / "i2.jpg"
    _make_file(v, b"v")
    _make_file(i1, b"i1")
    _make_file(i2, b"i2")
    items = [
        BrollItem(kind="image", source=i1),
        BrollItem(kind="video", source=v),
        BrollItem(kind="image", source=i2),
    ]
    prepare_broll_in_project(items, proj)
    # Видео — нумеруется внутри видео; фото — внутри фото.
    assert (proj / "broll_001.mp4").exists()
    photos = sorted((proj / "photos").iterdir(), key=lambda p: p.name)
    assert len(photos) == 2
    assert photos[0].name.startswith("photo_001")
    assert photos[1].name.startswith("photo_002")


def test_prepare_broll_empty_list_does_nothing(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    prepare_broll_in_project([], proj)
    # Не должна создаваться photos/, не должно быть broll_*.
    assert list(proj.iterdir()) == []


# ── place_selfie_as_avatar ──────────────────────────────────────────────────

def test_place_selfie_as_avatar_copies_and_renames(tmp_path):
    subtitled = tmp_path / "subtitled.mp4"
    _make_file(subtitled, b"selfie-with-subs")
    proj = tmp_path / "p"
    proj.mkdir()

    out = place_selfie_as_avatar(subtitled, proj)
    assert out.name.startswith("avatar_") and out.suffix == ".mp4"
    assert out.exists()
    assert out.read_bytes() == b"selfie-with-subs"


def test_place_selfie_as_avatar_returns_under_project_dir(tmp_path):
    subtitled = tmp_path / "subtitled.mp4"
    _make_file(subtitled, b"x")
    proj = tmp_path / "p"
    proj.mkdir()
    out = place_selfie_as_avatar(subtitled, proj)
    assert out.parent == proj


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
