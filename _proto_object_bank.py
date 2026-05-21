"""Prototype: object bank for Maksim carousel — universe «Бизнес/мастерская».

Fetches a few CC0 objects from the Met Museum, isolates them (rembg),
processes into warm racing-duotone (black→orange). Output: transparent-PNG
objects ready to compose into a carousel slide.

This is a PROTOTYPE — 1 universe, ~6 objects, to validate quality before
building the full 4-universe bank. Run: python _proto_object_bank.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import requests
from PIL import Image, ImageOps, ImageEnhance
from rembg import remove

if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1/search"
MET_OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{}"
UA = {"User-Agent": "maksim-carousel-proto/0.1 (gambi1t1@gmail.com)"}

# Objects biased to SPEED / MOVEMENT / ACHIEVEMENT — Maksim's brand energy.
# Wide list — the strict keyword filter rejects garbage, so ~8-12 will pass.
QUERIES = [
    "chronometer", "watch", "compass", "globe", "sextant", "armillary",
    "telescope", "spur", "hourglass", "wheel", "clock", "sundial",
    "barometer", "astrolabe", "orrery", "spyglass", "pendulum", "gyroscope",
    "helmet", "trophy", "medal", "stopwatch", "binnacle", "quadrant",
]

# Met classifications that are NOT isolatable objects — skip these.
SKIP_CLASSIFICATIONS = {
    "paintings", "drawings", "prints", "photographs", "books",
    "negatives", "textiles-woven", "drawings & prints", "ephemera",
}

OUT = Path(__file__).parent / "_proto_object_bank"
RAW = OUT / "raw"
ISO = OUT / "isolated"
BRANDED = OUT / "branded"
for d in (RAW, ISO, BRANDED):
    d.mkdir(parents=True, exist_ok=True)

# Warm racing-duotone — Life Drive palette.
ORANGE = (242, 102, 34)   # #F26622
BLACK = (12, 12, 12)


def fetch_met(query: str):
    """Return (image_url, title, object_id) of first PD *object* with an image.

    Filters out paintings/drawings/prints (not isolatable objects) and
    requires the query keyword to appear in objectName/title — Met search
    is loose and otherwise returns a Degas painting for «pocket watch».
    """
    try:
        r = requests.get(MET_SEARCH, params={"q": query, "hasImages": "true"},
                          headers=UA, timeout=30)
        ids = (r.json() or {}).get("objectIDs") or []
    except Exception as e:
        print(f"  search failed {query}: {e}")
        return None, None, None
    stem = query.lower().rstrip("s")[:5]   # «chronometer»→«chron», «watch»→«watch»
    for oid in ids[:80]:
        try:
            o = requests.get(MET_OBJECT.format(oid), headers=UA, timeout=30).json()
        except Exception:
            continue
        if not (o.get("isPublicDomain") and o.get("primaryImage")):
            continue
        classification = (o.get("classification") or "").lower()
        if any(skip in classification for skip in SKIP_CLASSIFICATIONS):
            continue
        # STRICT: query stem must appear in objectName or title. Met search is
        # loose — without this it returns «Book of the Dead» for «sextant».
        haystack = (o.get("objectName", "") + " " + o.get("title", "")).lower()
        if stem not in haystack:
            continue
        return o["primaryImage"], o.get("title", "object"), oid
    return None, None, None


def warm_duotone(im: Image.Image) -> Image.Image:
    """Grayscale → black→orange colorize (Life Drive racing recipe)."""
    g = ImageOps.grayscale(im)
    g = ImageEnhance.Contrast(g).enhance(1.12)
    return ImageOps.colorize(g, black=BLACK, white=ORANGE)


def main():
    ok = 0
    for q in QUERIES:
        slug = q.replace("'", "").replace(" ", "_")
        url, title, oid = fetch_met(q)
        if not url:
            print(f"MISS  {q}")
            continue
        try:
            img_bytes = requests.get(url, headers=UA, timeout=90).content
            raw = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception as e:
            print(f"FAIL  {q}: download/open {e}")
            continue
        raw.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
        raw.save(RAW / f"{slug}.jpg", quality=90)

        # Isolate object (rembg → transparent bg)
        try:
            cut = remove(raw)  # RGBA
        except Exception as e:
            print(f"FAIL  {q}: rembg {e}")
            continue
        cut.save(ISO / f"{slug}.png")

        # Warm duotone applied to the object, alpha preserved
        alpha = cut.split()[-1]
        duo = warm_duotone(cut.convert("RGB"))
        duo.putalpha(alpha)
        duo.save(BRANDED / f"{slug}_duo.png")

        ok += 1
        print(f"OK    {q}  ←  {title[:46]}  (Met #{oid})")

    print(f"\n{ok}/{len(QUERIES)} objects → {BRANDED}")


if __name__ == "__main__":
    main()
