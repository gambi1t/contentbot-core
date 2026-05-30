"""Test that _collect_stock_candidates throttles clips per (source, author).

Reason: 27 May 2026 — Pexels returned 4 clips from videographer Jakub Zerdzicki,
2 of which were visual duplicates. Without per-author cap we showed all 4.

Style matches test_brand_library_routing.py — no pytest, main() -> 0/1.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    status = "OK" if cond else "FAIL"
    print(f"  {status} {msg}")
    if not cond:
        errors.append(msg)


def _make_clip(source: str, clip_id: int, author_id: int, author_name: str = "test") -> dict:
    return {
        "id": f"{source.lower()}_{clip_id}",
        "source": source,
        "author_id": f"{source.lower()}_user_{author_id}",
        "author_name": author_name,
        "url": f"http://example/{clip_id}.mp4",
        "duration": 5,
        "width": 1080,
        "height": 1920,
        "tags": "",
    }


def test_throttles_per_author(errors: list[str]) -> None:
    print("\n-- per-author cap (Pexels: 4 from Jakub -> keep 2) --")

    pexels_results = [
        _make_clip("Pexels", 101, 555, "Jakub Zerdzicki"),
        _make_clip("Pexels", 102, 555, "Jakub Zerdzicki"),
        _make_clip("Pexels", 103, 555, "Jakub Zerdzicki"),
        _make_clip("Pexels", 104, 555, "Jakub Zerdzicki"),
        _make_clip("Pexels", 105, 777, "Other Author"),
    ]

    def fake_pexels(q, count=10):
        return pexels_results

    def fake_pixabay(q, count=10):
        return []

    orig_pex = bot._search_pexels_videos
    orig_pix = bot._search_pixabay_videos
    try:
        bot._search_pexels_videos = fake_pexels
        bot._search_pixabay_videos = fake_pixabay
        out = bot._collect_stock_candidates(["coffee"])
    finally:
        bot._search_pexels_videos = orig_pex
        bot._search_pixabay_videos = orig_pix

    jakub = [c for c in out if c["author_id"] == "pexels_user_555"]
    other = [c for c in out if c["author_id"] == "pexels_user_777"]

    _assert(len(jakub) == 2, f"keep exactly 2 from Jakub (got {len(jakub)})", errors)
    _assert(len(other) == 1, f"keep 1 from other author (got {len(other)})", errors)
    _assert(len(out) == 3, f"total = 3 (2+1, got {len(out)})", errors)


def test_dedup_same_clip_across_queries(errors: list[str]) -> None:
    print("\n-- same clip id across queries deduped --")
    clip = _make_clip("Pexels", 200, 888, "Author X")

    def fake_pexels(q, count=10):
        return [clip]

    def fake_pixabay(q, count=10):
        return []

    orig_pex = bot._search_pexels_videos
    orig_pix = bot._search_pixabay_videos
    try:
        bot._search_pexels_videos = fake_pexels
        bot._search_pixabay_videos = fake_pixabay
        out = bot._collect_stock_candidates(["coffee", "laptop", "writing"])
    finally:
        bot._search_pexels_videos = orig_pex
        bot._search_pixabay_videos = orig_pix

    _assert(len(out) == 1, f"same id collapsed to 1 (got {len(out)})", errors)


def test_mixed_pexels_and_pixabay(errors: list[str]) -> None:
    print("\n-- separate author caps per source --")
    # Same numeric user_id 555 but different sources -> counted separately
    # (since author_key is source-prefixed).
    pex = [_make_clip("Pexels", i, 555, "P-author") for i in range(300, 305)]
    pix = [_make_clip("Pixabay", i, 555, "Px-author") for i in range(400, 405)]

    def fake_pexels(q, count=10):
        return pex

    def fake_pixabay(q, count=10):
        return pix

    orig_pex = bot._search_pexels_videos
    orig_pix = bot._search_pixabay_videos
    try:
        bot._search_pexels_videos = fake_pexels
        bot._search_pixabay_videos = fake_pixabay
        out = bot._collect_stock_candidates(["coffee"])
    finally:
        bot._search_pexels_videos = orig_pex
        bot._search_pixabay_videos = orig_pix

    pex_kept = [c for c in out if c["source"] == "Pexels"]
    pix_kept = [c for c in out if c["source"] == "Pixabay"]
    _assert(len(pex_kept) == 2, f"Pexels capped at 2 (got {len(pex_kept)})", errors)
    _assert(len(pix_kept) == 2, f"Pixabay capped at 2 (got {len(pix_kept)})", errors)


def test_searcher_failure_does_not_kill_loop(errors: list[str]) -> None:
    print("\n-- one searcher raises -> other still works --")

    def bad_pexels(q, count=10):
        raise RuntimeError("API down")

    def fake_pixabay(q, count=10):
        return [_make_clip("Pixabay", 500, 999, "Survivor")]

    orig_pex = bot._search_pexels_videos
    orig_pix = bot._search_pixabay_videos
    try:
        bot._search_pexels_videos = bad_pexels
        bot._search_pixabay_videos = fake_pixabay
        out = bot._collect_stock_candidates(["coffee"])
    finally:
        bot._search_pexels_videos = orig_pex
        bot._search_pixabay_videos = orig_pix

    _assert(len(out) == 1, f"survived with 1 Pixabay clip (got {len(out)})", errors)
    _assert(out[0]["source"] == "Pixabay", "the survivor is Pixabay", errors)


def main() -> int:
    print("=" * 60)
    print("test_stock_dedup_by_author")
    print("=" * 60)
    errors: list[str] = []
    test_throttles_per_author(errors)
    test_dedup_same_clip_across_queries(errors)
    test_mixed_pexels_and_pixabay(errors)
    test_searcher_failure_does_not_kill_loop(errors)

    print()
    if errors:
        print(f"FAIL: {len(errors)} assertion(s) failed")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
