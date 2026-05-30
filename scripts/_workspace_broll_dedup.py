"""Post-process: deduplicate downloaded workspace candidates by Pexels/Pixabay id,
flag clips where filename hints at an actor in frame (man typing / person using).

Artём reviews the cleaned list and deletes anything aesthetically off.
"""
from __future__ import annotations
import re
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "broll-library" / "_workspace_candidates"

ACTOR_HINTS = re.compile(r"\b(man|woman|person|people|content_creator|persons)\b", re.IGNORECASE)

files = sorted(OUT_DIR.glob("*.mp4"))
print(f"Files before dedup: {len(files)}")

# Pattern: NN_<source>_<id>_<author>.mp4
seen_id: dict[str, Path] = {}
deleted = 0
kept: list[Path] = []
for p in files:
    m = re.match(r"\d+_(pexels|pixabay)_(\d+)_", p.name)
    if not m:
        kept.append(p)
        continue
    key = f"{m.group(1)}_{m.group(2)}"
    if key in seen_id:
        # Keep the first (earlier slot), delete this one
        p.unlink()
        deleted += 1
        print(f"  deleted dup: {p.name}  (kept: {seen_id[key].name})")
    else:
        seen_id[key] = p
        kept.append(p)

print(f"\nDeleted {deleted} duplicates by id, kept {len(kept)} files")

# Renumber files for cleanliness
print("\nRenumbering ...")
kept_sorted = sorted(kept, key=lambda x: x.name)
renamed: list[Path] = []
for i, p in enumerate(kept_sorted, 1):
    stem = p.stem
    # strip leading "NN_"
    stem_clean = re.sub(r"^\d+_", "", stem)
    new_name = f"{i:02d}_{stem_clean}{p.suffix}"
    new_path = p.with_name(new_name)
    if new_path != p:
        p.rename(new_path)
        renamed.append(new_path)
    else:
        renamed.append(p)

# Rewrite INDEX.md
idx_path = OUT_DIR / "INDEX.md"
old_lines = idx_path.read_text(encoding="utf-8").splitlines()
# parse old table to keep tags/url/query info
old_rows: dict[str, tuple[str, str, str, str, str, str, str]] = {}
# columns: # | File | Source | Author | Query | Duration | Resolution | Tags | Page
for ln in old_lines:
    if not ln.startswith("| ") or ln.startswith("| #") or ln.startswith("|---"):
        continue
    cols = [c.strip() for c in ln.strip("|").split("|")]
    if len(cols) < 9:
        continue
    file_cell = cols[1].strip("`")
    old_rows[file_cell] = (cols[2], cols[3], cols[4], cols[5], cols[6], cols[7], cols[8])

new_lines = [
    "# Workspace B-roll candidates (deduped)",
    "",
    f"Source: Pexels + Pixabay  ·  Total after dedup: {len(renamed)}",
    "",
    "**Policy:** только нейтральная рабочая обстановка (ноутбук/блокнот/кофе/типография).",
    "**Просмотреть глазами:** колонка ⚠️ помечает клипы, в названии которых может быть actor.",
    "Удалить такой клип, если в кадре лицо видно → останутся 25-35 чистых.",
    "",
    "| # | File | ⚠️ | Source | Author | Query | Duration | Resolution | Page |",
    "|---|------|----|--------|--------|-------|----------|------------|------|",
]
for i, p in enumerate(renamed, 1):
    # find matching old row
    old_key = None
    for k in old_rows:
        old_clean = re.sub(r"^\d+_", "", k)
        new_clean = re.sub(r"^\d+_", "", p.name)
        if old_clean == new_clean:
            old_key = k
            break
    if old_key:
        src, author, query, dur, res, tags, page = old_rows[old_key]
    else:
        src = author = query = dur = res = tags = page = "—"
    actor_flag = "👤" if ACTOR_HINTS.search(p.stem) or ACTOR_HINTS.search(tags) else ""
    new_lines.append(
        f"| {i} | `{p.name}` | {actor_flag} | {src} | {author} | {query} | {dur} | {res} | {page} |"
    )
idx_path.write_text("\n".join(new_lines), encoding="utf-8")

actors = sum(1 for ln in new_lines if "| 👤 |" in ln)
print(f"\nINDEX.md rewritten. Actor-flagged: {actors}/{len(renamed)}")
print(f"Folder: {OUT_DIR}")
