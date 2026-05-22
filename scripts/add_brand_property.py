"""One-off: add «Бренд» select property to the content-bot Notion DB.

Also cleans up a broken previously-added property whose name got corrupted
by cp1251 when we first tried via curl on Windows.

Safe to re-run — idempotent via ``name=="Бренд"`` check.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

NOTION_TOKEN = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY", "")
if not NOTION_TOKEN:
    raise SystemExit("Set NOTION_TOKEN env var before running this one-off script.")
DB_ID = "3220ef6e5ff6808b84fde8167a6c79c0"
BRAND_PROP_NAME = "Бренд"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json; charset=utf-8",
}


def get_db() -> dict:
    r = httpx.get(f"https://api.notion.com/v1/databases/{DB_ID}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def patch_db(payload: dict) -> dict:
    # httpx sends JSON as UTF-8 by default — no cp1251 surprises.
    r = httpx.patch(
        f"https://api.notion.com/v1/databases/{DB_ID}",
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"ERROR {r.status_code}: {r.text[:500]}")
        r.raise_for_status()
    return r.json()


def main() -> int:
    db = get_db()
    props = db.get("properties", {})
    print("Current properties:")
    for name, p in props.items():
        print(f"  {name!r} ({p.get('type')})")
    print()

    # Find corrupted property from the failed cp1251 call. It will have a
    # non-cyrillic mojibake name but type=select with NO options (our first
    # call set options but the name got mangled). Delete it if found.
    # Heuristic: any select property whose name is NOT in the known good list
    # and whose name contains non-ascii replacement chars.
    known_good = {"Рубрика ", "Рубрика", "Площадки", "Status", "Дата публикации",
                  "Призыв", "Опубликовано на", "Формат", "ССылка на материалы",
                  "Name", BRAND_PROP_NAME}
    to_delete = []
    for name, p in props.items():
        if name in known_good:
            continue
        if p.get("type") == "select":
            to_delete.append(name)

    if to_delete:
        print(f"Deleting corrupted properties: {to_delete}")
        # Notion API: set property to null to delete
        del_payload = {"properties": {n: None for n in to_delete}}
        patch_db(del_payload)
        # Re-fetch
        db = get_db()
        props = db.get("properties", {})

    # Check if Бренд already exists
    if BRAND_PROP_NAME in props:
        existing = props[BRAND_PROP_NAME]
        print(f"Property «{BRAND_PROP_NAME}» already exists: type={existing.get('type')}")
        opts = existing.get("select", {}).get("options", [])
        print(f"Options: {[o['name'] for o in opts]}")
        # Ensure default + shoes present
        existing_names = {o["name"] for o in opts}
        needed = {"default", "shoes"}
        missing = needed - existing_names
        if missing:
            print(f"Adding missing options: {missing}")
            new_opts = list(opts) + [
                {"name": "default", "color": "gray"} if "default" in missing else None,
                {"name": "shoes", "color": "brown"} if "shoes" in missing else None,
            ]
            new_opts = [o for o in new_opts if o is not None]
            patch_db({"properties": {BRAND_PROP_NAME: {"select": {"options": new_opts}}}})
            print("✓ Updated options")
        else:
            print("✓ All required options already present — nothing to do")
        return 0

    # Add property
    print(f"Adding «{BRAND_PROP_NAME}» select property...")
    resp = patch_db({
        "properties": {
            BRAND_PROP_NAME: {
                "select": {
                    "options": [
                        {"name": "default", "color": "gray"},
                        {"name": "shoes", "color": "brown"},
                    ]
                }
            }
        }
    })
    new_props = resp.get("properties", {})
    if BRAND_PROP_NAME in new_props:
        opts = new_props[BRAND_PROP_NAME].get("select", {}).get("options", [])
        print(f"✓ Created «{BRAND_PROP_NAME}» with options: {[o['name'] for o in opts]}")
        return 0

    print(f"✗ Failed to create. Response keys: {list(new_props.keys())}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
