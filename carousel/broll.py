"""B-roll photo picker for carousel inner-slide backgrounds.

Addresses Maksim's «пустовато» feedback (14 May 2026): inner slides looked
empty. Solution — a darkened B-roll photo underlay (~88-92% black veil on
top) that adds atmosphere without hurting text readability.

Archive on server: /home/maksim-bot/maksim-bot/broll-library/photos/maksim/
  scenes: glamping (49) / karting (45) / meetings (8) / sup (11)
  each photo has a <name>.json sidecar with quality_grade / has_people / scene.

Graceful degradation: if the archive is missing (e.g. local dev) or a scene
is empty, `pick_background_photos` returns [] and the renderer falls back to
plain dark slides — nothing breaks.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

# Default archive root on the production server. Overridable for local tests.
DEFAULT_ARCHIVE_ROOT = Path(
    "/home/maksim-bot/maksim-bot/broll-library/photos/maksim"
)

# Theme keyword → scene folder. v1 carousels lean on Maksim's personal /
# entrepreneurship brand, so the default scene is «meetings» (business mood).
_SCENE_KEYWORDS: dict[str, list[str]] = {
    "karting": ["картинг", "карт", "трасс", "заезд", "гонк", "болид",
                "пилот", "руль", "скорост"],
    "glamping": ["глэмпинг", "глемпинг", "глэмп", "домик", "природ",
                 "лес", "отдых", "туризм", "бронир"],
    "sup": ["sup", "сап", "сапборд", "доск", "вода", "река", "озер"],
    "meetings": ["бизнес", "предприним", "команд", "встреч", "перегов",
                 "офис", "работ", "деньг", "клиент", "продукт", "найм",
                 "сотрудник", "партнёр", "партнер", "делег", "операц"],
}

_IMG_EXT = (".jpg", ".jpeg", ".png")


def _detect_scene(theme: str) -> str:
    """Map a carousel theme to a B-roll scene folder.

    Counts keyword hits per scene; ties and zero-hit → «meetings» (business
    atmosphere — the safe default for personal-brand / entrepreneurship).
    """
    t = (theme or "").lower()
    best, best_hits = "meetings", 0
    for scene, kws in _SCENE_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in t)
        if hits > best_hits:
            best, best_hits = scene, hits
    return best


def pick_background_photos(
    theme: str,
    count: int,
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
) -> list[Path]:
    """Pick `count` background photos matching the carousel theme.

    Strategy:
      1. Detect scene from theme keywords.
      2. Glob photos in that scene; drop quality_grade == 'weak'.
      3. Prefer has_people == false (cleaner backgrounds); fallback to all.
      4. Shuffle; cycle if fewer than `count`.

    Returns list of `count` Path objects, or [] on any failure (missing
    archive / empty scene) — caller then renders plain dark slides.
    """
    archive_root = Path(archive_root)
    scene = _detect_scene(theme)
    scene_dir = archive_root / scene

    if not scene_dir.is_dir():
        logger.warning(f"[carousel-broll] scene dir missing: {scene_dir}, "
                        f"trying 'meetings' fallback")
        scene_dir = archive_root / "meetings"
        scene = "meetings"
        if not scene_dir.is_dir():
            logger.warning(f"[carousel-broll] archive unavailable: {archive_root}")
            return []

    photos = sorted(
        p for p in scene_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMG_EXT
    )
    if not photos:
        logger.warning(f"[carousel-broll] no photos in {scene_dir}")
        return []

    # Read sidecar metadata, filter weak quality, track has_people.
    graded: list[tuple[Path, bool]] = []
    for p in photos:
        meta_path = p.with_name(p.name + ".json")
        quality, has_people = "broll", False
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                quality = meta.get("quality_grade", "broll")
                has_people = bool(meta.get("has_people", False))
            except Exception:
                pass
        if quality == "weak":
            continue
        graded.append((p, has_people))

    if not graded:
        graded = [(p, False) for p in photos]

    # Prefer photos without people for cleaner backgrounds; fall back to all
    # if there aren't enough no-people shots.
    no_people = [p for p, hp in graded if not hp]
    pool = no_people if len(no_people) >= count else [p for p, _ in graded]
    if not pool:
        return []

    random.shuffle(pool)
    result: list[Path] = []
    i = 0
    while len(result) < count:
        result.append(pool[i % len(pool)])
        i += 1

    logger.info(
        f"[carousel-broll] scene={scene}, archive has {len(graded)} usable, "
        f"picked {len(result)} for {count} slots"
    )
    return result


__all__ = ["pick_background_photos", "DEFAULT_ARCHIVE_ROOT"]
