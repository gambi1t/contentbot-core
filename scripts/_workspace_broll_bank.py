"""Pull ~40 candidate B-roll clips for the 'workspace' library (neutral office life).

Policy (project_maksim_broll_stock_policy.md):
- ALLOWED: hands typing, writing in notebook, laptop+coffee general view,
  closed notebook, pages turning, espresso, typography close-up.
- BLOCKED: any face in frame, any actor (man/woman/businessman), handshakes,
  meetings, AI-generated stock.

Source: Pexels + Pixabay. Dedupe by (source, user_id). Max 3 per author.
Output: D:\\AI\\maksim-bot\\broll-library\\_workspace_candidates\\
  - <source>_<id>_<author>.mp4
  - INDEX.md with table for human review
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
for line in env_path.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import httpx

PEXELS_KEY = os.environ["PEXELS_API_KEY"]
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"]

OUT_DIR = ROOT / "broll-library" / "_workspace_candidates"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Queries focused on objects and workflow, NOT actors.
QUERIES = [
    "typing keyboard laptop",
    "writing notebook hand",
    "laptop coffee desk",
    "notebook pen writing",
    "espresso morning workspace",
    "hands typing computer closeup",
    "coffee cup desk laptop",
    "office desk laptop closeup",
    "pages turning notebook",
    "typography close up",
]

# Tag-based filter
FORBIDDEN_TAGS = {
    "woman", "women", "female", "girl", "lady", "wife", "mother", "businesswoman",
    "child", "kid", "boy", "family",
    "face", "portrait", "smile", "smiling", "laughing", "talking", "speaking",
    "handshake", "meeting", "people", "crowd", "audience",
    "ai generated", "ai-generated", "aigenerated",
    # Note: 'man' / 'businessman' are too generic (laptops often tagged with them) —
    # we drop only if MULTIPLE forbidden tags or explicit face/portrait.
}
SOFT_FORBIDDEN = {"man", "businessman", "businessperson", "person"}

def is_face_clip(tags: str) -> bool:
    t = tags.lower()
    if any(re.search(rf"\b{re.escape(w)}\b", t) for w in FORBIDDEN_TAGS):
        return True
    # If 2+ "soft" person-words AND no object words → likely actor-focused
    soft_hits = sum(1 for w in SOFT_FORBIDDEN if re.search(rf"\b{re.escape(w)}\b", t))
    object_hits = sum(1 for w in ("laptop", "keyboard", "notebook", "pen", "coffee", "desk", "typing", "writing", "paper")
                      if w in t)
    if soft_hits >= 1 and object_hits == 0:
        return True
    return False


PER_AUTHOR_MAX = 3
seen: dict[tuple[str, str], int] = {}  # (source, user_id) → count
candidates: list[dict] = []


def add(c: dict) -> bool:
    key = (c["source"], str(c["user_id"]))
    if seen.get(key, 0) >= PER_AUTHOR_MAX:
        return False
    seen[key] = seen.get(key, 0) + 1
    candidates.append(c)
    return True


def search_pexels(q: str, per_page: int = 20):
    r = httpx.get(
        "https://api.pexels.com/videos/search",
        params={"query": q, "per_page": per_page},
        headers={"Authorization": PEXELS_KEY},
        timeout=20,
    )
    r.raise_for_status()
    for v in r.json().get("videos", []):
        dur = v.get("duration", 0)
        if dur < 3 or dur > 15:
            continue
        # Pexels has no tags by default; use 'tags' if present + url slug
        url = v.get("url", "")
        slug_tags = url.split("/")[-2] if "/" in url else ""
        if is_face_clip(slug_tags):
            continue
        # Pick smallest HD file ≥720 wide
        files = sorted(
            [f for f in v.get("video_files", []) if f.get("width", 0) >= 720 and f.get("file_type") == "video/mp4"],
            key=lambda f: f.get("width", 9999),
        )
        if not files:
            continue
        hd = files[0]
        user = v.get("user", {})
        add({
            "source": "pexels",
            "id": v["id"],
            "user_id": user.get("id"),
            "user_name": user.get("name", "unknown"),
            "duration": dur,
            "width": hd["width"],
            "height": hd["height"],
            "tags": slug_tags,
            "page": v.get("url"),
            "download": hd["link"],
            "query": q,
        })


def search_pixabay(q: str, per_page: int = 20):
    r = httpx.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_KEY, "q": q, "per_page": per_page, "safesearch": "true"},
        timeout=20,
    )
    r.raise_for_status()
    for v in r.json().get("hits", []):
        dur = v.get("duration", 0)
        if dur < 3 or dur > 15:
            continue
        tags = v.get("tags", "") or ""
        if is_face_clip(tags):
            continue
        files = v.get("videos", {})
        vf = files.get("large") or files.get("medium")
        if not vf or vf.get("width", 0) < 720:
            continue
        add({
            "source": "pixabay",
            "id": v["id"],
            "user_id": v.get("user_id"),
            "user_name": v.get("user", "unknown"),
            "duration": dur,
            "width": vf["width"],
            "height": vf["height"],
            "tags": tags,
            "page": v.get("pageURL"),
            "download": vf["url"],
            "query": q,
            "size_mb": round(vf.get("size", 0) / 1024 / 1024, 1),
        })


print(f"\nQueries: {len(QUERIES)}")
for q in QUERIES:
    n_before = len(candidates)
    try:
        search_pexels(q, per_page=15)
        search_pixabay(q, per_page=15)
    except Exception as e:
        print(f"  ! {q}: {type(e).__name__}: {e}")
        continue
    print(f"  {q}: +{len(candidates) - n_before}")

print(f"\nTotal candidates after filter: {len(candidates)}\n")

# Cap at 50 most diverse-by-query
seen_pairs: set[str] = set()
final: list[dict] = []
for q in QUERIES:
    n = 0
    for c in candidates:
        if c["query"] != q:
            continue
        if n >= 6:
            break
        final.append(c)
        n += 1
for c in candidates:
    if len(final) >= 50:
        break
    if c not in final:
        final.append(c)

final = final[:50]
print(f"Selected for download: {len(final)}\n")

# Download
ok, fail = 0, 0
for i, c in enumerate(final, 1):
    safe_author = re.sub(r"[^a-zA-Z0-9_]", "_", str(c["user_name"]))[:30]
    fname = f"{i:02d}_{c['source']}_{c['id']}_{safe_author}.mp4"
    dest = OUT_DIR / fname
    if dest.exists():
        ok += 1
        print(f"  [{i}/{len(final)}] {fname}  (cached)")
        continue
    try:
        with httpx.stream("GET", c["download"], timeout=60, follow_redirects=True) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
        size_mb = dest.stat().st_size / 1024 / 1024
        c["local"] = fname
        c["local_size_mb"] = round(size_mb, 1)
        ok += 1
        print(f"  [{i}/{len(final)}] {fname}  ({size_mb:.1f} MB)")
    except Exception as e:
        fail += 1
        print(f"  [{i}/{len(final)}] FAIL {fname}: {e}")

print(f"\nDownloaded {ok}, failed {fail}")

# Write INDEX.md for human review
lines = [
    "# Workspace B-roll candidates",
    "",
    f"Source: Pexels + Pixabay  ·  Queries: {len(QUERIES)}  ·  Downloaded: {ok}",
    "",
    "**Policy:** только нейтральная рабочая обстановка (ноутбук/блокнот/кофе/типография). Никаких людей-актёров.",
    "",
    "| # | File | Source | Author | Query | Duration | Resolution | Tags | Page |",
    "|---|------|--------|--------|-------|----------|------------|------|------|",
]
for i, c in enumerate(final, 1):
    fname = c.get("local", "—")
    tags_short = (c["tags"] or "")[:80]
    lines.append(
        f"| {i} | `{fname}` | {c['source']} | {c['user_name']} | {c['query']} | "
        f"{c['duration']}s | {c['width']}x{c['height']} | {tags_short} | "
        f"[link]({c['page']}) |"
    )
(OUT_DIR / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
print(f"\nIndex: {OUT_DIR / 'INDEX.md'}")
print(f"Folder: {OUT_DIR}")
