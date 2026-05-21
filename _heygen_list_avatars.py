"""Инвентаризация аккаунта HeyGen — все аватары, фото-аватар-группы,
talking photos. Чтобы видеть всё одним списком, без UI.

Запуск НА СЕРВЕРЕ:  python _heygen_list_avatars.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

HERE = Path(__file__).parent


def _key() -> str:
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("HEYGEN_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _show(label: str, items: list, fields: list[str]) -> None:
    print(f"\n=== {label}: {len(items)} ===")
    for it in items:
        if not isinstance(it, dict):
            print(f"  {it}")
            continue
        parts = []
        for f in fields:
            if f in it and it[f] not in (None, ""):
                parts.append(f"{f}={it[f]}")
        # добор полей, которых нет в списке fields
        extra = [k for k in it if k not in fields][:4]
        for k in extra:
            parts.append(f"{k}={it[k]}")
        print("  " + " | ".join(str(p) for p in parts))


def main() -> int:
    key = _key()
    if not key:
        print("FAIL: нет HEYGEN_API_KEY")
        return 1
    h = {"X-Api-Key": key, "Accept": "application/json"}

    with httpx.Client(timeout=30) as c:
        # 1. Фото-аватар-группы (именно они упираются в лимит)
        r = c.get("https://api.heygen.com/v2/avatar_group.list", headers=h)
        print(f"[avatar_group.list] HTTP {r.status_code}")
        try:
            d = r.json().get("data", {})
            groups = d.get("avatar_group_list") or d.get("groups") or d.get("list") or []
            junk = [g for g in groups if g.get("name") == "Avatar IV Video"]
            named = [g for g in groups if g.get("name") != "Avatar IV Video"]
            print(f"\n=== ФОТО-АВАТАР-ГРУППЫ: {len(groups)} всего ===")
            print(f"  • 'Avatar IV Video' — авто-мусор Image-to-Video: {len(junk)}")
            print(f"  • Именованные (возможно нужные): {len(named)}")
            print("\n  ИМЕНОВАННЫЕ:")
            for g in named:
                print(f"    {g.get('id')} | {g.get('name')!r} | "
                      f"looks={g.get('num_looks')} | train={g.get('train_status')}")
        except Exception as e:
            print(f"  parse error: {e} — body: {r.text[:300]}")

        # 2. Обычные аватары + talking photos
        r = c.get("https://api.heygen.com/v2/avatars", headers=h)
        print(f"\n[avatars] HTTP {r.status_code}")
        try:
            d = r.json().get("data", {})
            _show("АВАТАРЫ", d.get("avatars") or [],
                  ["avatar_id", "avatar_name", "gender", "premium"])
            _show("TALKING PHOTOS", d.get("talking_photos") or [],
                  ["talking_photo_id", "talking_photo_name"])
        except Exception as e:
            print(f"  parse error: {e} — body: {r.text[:300]}")

        # 3. Остаток квоты
        r = c.get("https://api.heygen.com/v2/user/remaining_quota", headers=h)
        try:
            print(f"\n[remaining_quota] {r.json().get('data', {})}")
        except Exception:
            print(f"\n[remaining_quota] HTTP {r.status_code}: {r.text[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
