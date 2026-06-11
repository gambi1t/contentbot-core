"""Set «Бренд» = default on old Notion cards where the property is empty.

Phase 2 brand-system migration (2026-04-22). The «Бренд» select property was
added on 19 Apr. Cards created before that date (or created manually) have the
field empty, which breaks our new ContextVar-driven brand resolution: the card
no longer announces "I'm a default brand card", it announces nothing.

Fix: walk the whole content DB, find cards with an empty «Бренд», set it to
``default``. Non-default cards (shoes, future brands) already have the field
filled in — we never overwrite.

Safe to re-run — only touches cards with a *missing* select value.

Usage:
    python scripts/set_brand_on_old_cards.py --dry-run   # default, lists what would change
    python scripts/set_brand_on_old_cards.py --apply     # actually writes
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

# Force UTF-8 console on Windows (cp1251 chokes on emojis + cyrillic).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

# Секрет — только из окружения (раньше был захардкожен; вычищено 11 июня 2026).
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DB_ID = os.environ.get("NOTION_DATABASE_ID", "3220ef6e5ff6808b84fde8167a6c79c0")
BRAND_PROP_PRIMARY = "Бренд"
BRAND_PROP_FALLBACK = "Brand"
DEFAULT_BRAND = "default"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json; charset=utf-8",
}


def query_all_cards() -> list[dict]:
    cards: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = httpx.post(
            f"https://api.notion.com/v1/databases/{DB_ID}/query",
            headers=HEADERS,
            json=body,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        chunk = data.get("results", [])
        cards.extend(chunk)
        page += 1
        print(f"  page {page}: +{len(chunk)} (total {len(cards)})")
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return cards


def card_title(card: dict) -> str:
    props = card.get("properties", {})
    name_prop = props.get("Name") or props.get("Название") or {}
    title_arr = name_prop.get("title", [])
    if not title_arr:
        return "(без названия)"
    return "".join(t.get("plain_text", "") for t in title_arr)[:80]


def get_brand_prop(card: dict) -> tuple[str | None, dict | None]:
    """Return (prop_name_used, prop_value) or (None, None) if missing entirely."""
    props = card.get("properties", {})
    for name in (BRAND_PROP_PRIMARY, BRAND_PROP_FALLBACK):
        if name in props:
            return name, props[name]
    return None, None


def is_empty_brand(prop_value: dict | None) -> bool:
    if not prop_value:
        return True
    if prop_value.get("type") != "select":
        # Unexpected shape — don't touch.
        return False
    return prop_value.get("select") is None


def patch_card(card_id: str, prop_name: str, brand: str) -> None:
    r = httpx.patch(
        f"https://api.notion.com/v1/pages/{card_id}",
        headers=HEADERS,
        json={"properties": {prop_name: {"select": {"name": brand}}}},
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"    ✗ ERROR {r.status_code}: {r.text[:300]}")
        r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write changes")
    ap.add_argument("--dry-run", action="store_true", help="show what would change (default)")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== Brand migration — {mode} ===")
    print(f"DB: {DB_ID}")
    print(f"Default brand: {DEFAULT_BRAND!r}")
    print()

    print("Fetching all cards...")
    cards = query_all_cards()
    print(f"Total cards: {len(cards)}\n")

    to_fix: list[tuple[str, str, str]] = []  # (card_id, prop_name, title)
    missing_prop: list[tuple[str, str]] = []  # (card_id, title)
    has_brand: int = 0

    for card in cards:
        title = card_title(card)
        prop_name, prop_value = get_brand_prop(card)
        if prop_name is None:
            # Property doesn't even exist on this card's parent — shouldn't happen
            # after add_brand_property.py, but log and skip.
            missing_prop.append((card["id"], title))
            continue
        if is_empty_brand(prop_value):
            to_fix.append((card["id"], prop_name, title))
        else:
            has_brand += 1

    print(f"Cards with brand set:    {has_brand}")
    print(f"Cards with EMPTY brand:  {len(to_fix)}")
    print(f"Cards missing property:  {len(missing_prop)}  (should be 0)")
    print()

    if missing_prop:
        print("⚠️ Cards missing the brand property entirely:")
        for cid, title in missing_prop[:10]:
            print(f"  - {cid}  {title}")
        if len(missing_prop) > 10:
            print(f"  ... and {len(missing_prop) - 10} more")
        print()

    if not to_fix:
        print("✓ Nothing to do.")
        return 0

    print("Cards that will be set to «default»:")
    for cid, _prop, title in to_fix[:20]:
        print(f"  - {cid}  {title}")
    if len(to_fix) > 20:
        print(f"  ... and {len(to_fix) - 20} more")
    print()

    if not apply:
        print("Dry-run complete. Re-run with --apply to write changes.")
        return 0

    print("Writing...")
    ok = 0
    failed = 0
    for i, (cid, prop_name, title) in enumerate(to_fix, 1):
        try:
            patch_card(cid, prop_name, DEFAULT_BRAND)
            ok += 1
            print(f"  [{i}/{len(to_fix)}] ✓ {title}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [{i}/{len(to_fix)}] ✗ {title}: {e}")
        # Notion rate limit: ~3 req/s sustained. Sleep 0.35s.
        time.sleep(0.35)

    print()
    print(f"Done. OK={ok}  FAILED={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
