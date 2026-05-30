"""Test that _search_local_broll for brand=maksim NEVER pulls Артёмовы AI-categories.

Reason: 27 May 2026 — "50/50 роботам" incident. Maksim's scripts mentioning generic
tech words (technology / AI / server) leaked into category `tech-general` / `ai-tools`
because _search_local_broll used a global BROLL_CATEGORY_KEYWORDS map and globbed
BROLL_LIBRARY_DIR/<cat>/ (Артёмова структура) without brand awareness.
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


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    status = "OK" if cond else "FAIL"
    print(f"  {status} {msg}")
    if not cond:
        errors.append(msg)


def _make_mp4(path: Path) -> None:
    """Minimal non-empty file (size > 1000 bytes) to pass library filters."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 2048)


def with_temp_library(fn):
    def wrapper(errors: list[str]) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="broll_test_"))
        orig_dir = bot.BROLL_LIBRARY_DIR
        orig_photo = bot.PHOTO_LIBRARY_DIR
        bot.BROLL_LIBRARY_DIR = tmp
        bot.PHOTO_LIBRARY_DIR = tmp / "photos"
        try:
            fn(tmp, errors)
        finally:
            bot.BROLL_LIBRARY_DIR = orig_dir
            bot.PHOTO_LIBRARY_DIR = orig_photo
            shutil.rmtree(tmp, ignore_errors=True)
    return wrapper


@with_temp_library
def test_maksim_script_with_tech_word_does_not_pull_robots(tmp: Path, errors: list[str]) -> None:
    print("\n-- Максим: сценарий со словом 'технологии' НЕ тянет роботы --")
    # Setup: BOTH layouts coexist on disk.
    # Артёмова: broll-library/tech-general/robot1.mp4
    # Максимова: broll-library/clips/maksim/karting/clip1.mp4
    _make_mp4(tmp / "tech-general" / "robot1.mp4")
    _make_mp4(tmp / "ai-tools" / "claude_screen.mp4")
    _make_mp4(tmp / "clips" / "maksim" / "karting" / "kart_run.mp4")
    _make_mp4(tmp / "clips" / "maksim" / "glamping" / "glamp_view.mp4")

    # Script that mentions "технологии" — historic trigger of the bug
    clips = bot._search_local_broll(
        script_phrase="Использую технологии чтобы развивать картинг",
        visual_desc="картинг трасса",
        search_queries=["karting business"],
        brand="maksim",
    )

    paths = [c["path"] for c in clips]
    robots = [p for p in paths if "tech-general" in p.replace("\\", "/") or "ai-tools" in p.replace("\\", "/")]
    karting = [p for p in paths if "karting" in p.replace("\\", "/")]

    _assert(len(robots) == 0, f"NO Артём-AI clips in maksim output (got {len(robots)}: {robots})", errors)
    _assert(len(karting) >= 1, f"karting clip pulled (got {len(karting)})", errors)


@with_temp_library
def test_maksim_unmatched_text_returns_empty(tmp: Path, errors: list[str]) -> None:
    print("\n-- Максим: текст без Максим-keyword -> пусто, fallback на stock --")
    _make_mp4(tmp / "clips" / "maksim" / "karting" / "kart.mp4")
    _make_mp4(tmp / "tech-general" / "robot.mp4")

    clips = bot._search_local_broll(
        script_phrase="Думаю о будущем",  # ни одно слово не в MAKSIM_CLIP_KEYWORDS
        visual_desc="абстрактное размышление",
        search_queries=["thinking"],
        brand="maksim",
    )
    _assert(len(clips) == 0, f"empty when no maksim keyword matches (got {len(clips)})", errors)


@with_temp_library
def test_default_brand_still_works(tmp: Path, errors: list[str]) -> None:
    print("\n-- Артём (default): легаси-поведение сохранено --")
    _make_mp4(tmp / "tech-general" / "datacenter.mp4")
    _make_mp4(tmp / "ai-tools" / "claude.mp4")
    _make_mp4(tmp / "clips" / "maksim" / "karting" / "kart.mp4")

    # Default brand: keyword "технолог" -> tech-general -> datacenter.mp4
    clips = bot._search_local_broll(
        script_phrase="Эти технологии меняют мир",
        visual_desc="",
        search_queries=[],
        brand="default",  # this bot's .env sets DEFAULT_BRAND=maksim,
                           # so brand=None would also be 'maksim' — pass explicitly.
    )
    paths = [c["path"] for c in clips]
    has_tech = any("tech-general" in p.replace("\\", "/") for p in paths)
    has_maksim = any("clips/maksim" in p.replace("\\", "/") for p in paths)
    _assert(has_tech, "Артём's tech-general still works", errors)
    _assert(not has_maksim, "Артём does NOT pull from maksim's library", errors)


def main() -> int:
    print("=" * 60)
    print("test_local_broll_brand_aware")
    print("=" * 60)
    errors: list[str] = []
    test_maksim_script_with_tech_word_does_not_pull_robots(errors)
    test_maksim_unmatched_text_returns_empty(errors)
    test_default_brand_still_works(errors)

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
