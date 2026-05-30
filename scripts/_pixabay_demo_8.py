"""One-off: pull 8 demo videos from Pixabay across 4 business-narrative themes.

Themes (Hormozi-style B-roll for "explain a business topic"):
  1. thinking entrepreneur — задумчивый предприниматель, рост, видение
  2. writing planning     — расписывание планов в блокноте
  3. laptop morning coffee — утро, начало дня, фокус
  4. handshake meeting    — переговоры, сделки

Filters: duration 3-15s (Reels-friendly), HD ≥720px wide, dedupe by user_id
across themes so we don't get 4 clips from one author.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
for line in env_path.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import httpx

KEY = os.environ.get("PIXABAY_API_KEY")
if not KEY:
    sys.exit("PIXABAY_API_KEY not set")

THEMES = [
    ("Задумчивый предприниматель", "businessman thinking"),
    ("Планирование в блокноте",    "notebook writing planning"),
    ("Утро / ноутбук + кофе",      "laptop coffee morning"),
    ("Рукопожатие / переговоры",   "business handshake meeting"),
]

seen_user_ids: set[int] = set()
all_picks: list[dict] = []

for label, q in THEMES:
    resp = httpx.get(
        "https://pixabay.com/api/videos/",
        params={"key": KEY, "q": q, "per_page": 20, "safesearch": "true"},
        timeout=20,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", [])

    theme_picks: list[dict] = []
    for v in hits:
        dur = v.get("duration", 0)
        if dur < 3 or dur > 15:
            continue
        user_id = v.get("user_id")
        if user_id in seen_user_ids:
            continue
        # Hard rule (feedback_broll_male_only): no women/female hands in B-roll.
        tags_lower = (v.get("tags", "") or "").lower()
        FORBIDDEN = ("woman", "women", "female", "girl", "lady", "businesswoman", " she ", "she's", "wife", "mother")
        if any(w in tags_lower for w in FORBIDDEN):
            continue
        files = v.get("videos", {})
        vf = files.get("large") or files.get("medium")
        if not vf or vf.get("width", 0) < 720:
            continue
        theme_picks.append({
            "theme": label,
            "query": q,
            "id": v.get("id"),
            "user": v.get("user"),
            "user_id": user_id,
            "duration": dur,
            "tags": v.get("tags", ""),
            "page": v.get("pageURL"),
            "preview": f"https://i.vimeocdn.com/video/{v.get('picture_id', '')}_640x360.jpg",
            "download": vf.get("url"),
            "size_mb": round(vf.get("size", 0) / 1024 / 1024, 1),
            "width": vf.get("width"),
            "height": vf.get("height"),
        })
        if len(theme_picks) >= 2:
            break
    for p in theme_picks:
        seen_user_ids.add(p["user_id"])
    all_picks.extend(theme_picks)

print(f"\n=== Pixabay: {len(all_picks)} demo videos (deduped by author) ===\n")
for i, p in enumerate(all_picks, 1):
    print(f"#{i} [{p['theme']}] q='{p['query']}'")
    print(f"   author: {p['user']} (id={p['user_id']})")
    print(f"   duration: {p['duration']}s · {p['width']}x{p['height']} · {p['size_mb']} MB")
    print(f"   tags: {p['tags']}")
    print(f"   page: {p['page']}")
    print(f"   download: {p['download']}")
    print()
