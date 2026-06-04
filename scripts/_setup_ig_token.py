"""Собрать instagram_token.json из System User токена (читается из файла,
в argv/лог не попадает). Находит Страницу с привязанным IG @yumsunov86,
достаёт page_access_token + ig_user_id, пишет токен-файл в формате crosspost.py.

В stdout печатаются ТОЛЬКО не-секретные поля (имя Страницы, IG username, id).
Токены НЕ печатаются.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE_IN = ROOT / "token_insta.txt"
OUT_FILE = ROOT / "instagram_token.json"
TARGET_USERNAME = "yumsunov86"
GRAPH = "https://graph.facebook.com/v21.0"

# Берём ПОСЛЕДНЮЮ непустую строку — Артём дописывает новый токен с новой строки.
_lines = [l.strip() for l in TOKEN_FILE_IN.read_text(encoding="utf-8").splitlines() if l.strip()]
if not _lines:
    sys.exit("FAIL: пустой токен в token_insta.txt")
systoken = _lines[-1]
print(f"(использую последнюю строку токена из {len(_lines)})")

# 1) Кто мы (sanity) - НЕ печатаем токен
me = requests.get(f"{GRAPH}/me", params={"access_token": systoken, "fields": "id,name"}, timeout=20)
if me.status_code != 200:
    print(f"FAIL: токен невалиден или нет доступа: {me.status_code} {me.text[:200]}")
    sys.exit(1)
print(f"Токен OK. System user: {me.json().get('name')} (id={me.json().get('id')})")

# 2) Страницы + привязанный IG
pages = requests.get(
    f"{GRAPH}/me/accounts",
    params={"access_token": systoken, "fields": "name,access_token,instagram_business_account{id,username}"},
    timeout=20,
)
if pages.status_code != 200:
    print(f"FAIL: /me/accounts: {pages.status_code} {pages.text[:300]}")
    sys.exit(1)

data = pages.json().get("data", [])
print(f"Доступно Страниц: {len(data)}")
candidates = []
for p in data:
    ig = p.get("instagram_business_account") or {}
    uname = (ig.get("username") or "").lower()
    print(f"  - Страница '{p.get('name')}' (page_id={p.get('id')}) -> IG @{uname or '-'}")
    if ig.get("id"):
        candidates.append({
            "page_id": p["id"],
            "page_name": p.get("name", ""),
            "page_access_token": p.get("access_token", ""),
            "ig_user_id": ig["id"],
            "ig_username": uname,
        })

chosen = next((c for c in candidates if c["ig_username"] == TARGET_USERNAME), None)
if not chosen:
    print(f"\nFAIL: @{TARGET_USERNAME} не найден среди привязанных Страниц.")
    print("Проверь: System User назначен на Страницу 'Yumsunov Maksim' с полным доступом,")
    print("и эта Страница связана с @yumsunov86.")
    sys.exit(1)

if not chosen["page_access_token"]:
    print(f"\nFAIL: нет page_access_token для @{TARGET_USERNAME} - System User не имеет полного доступа к Странице.")
    sys.exit(1)

token_data = {
    "access_token": systoken,                       # System User token (не растухает)
    "page_id": chosen["page_id"],
    "page_access_token": chosen["page_access_token"],
    "ig_user_id": chosen["ig_user_id"],
    "obtained_at": time.time(),
    "source": "system_user",
    "ig_username": chosen["ig_username"],
}
OUT_FILE.write_text(json.dumps(token_data, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\nOK: instagram_token.json собран.")
print(f"  IG: @{chosen['ig_username']} (ig_user_id={chosen['ig_user_id']})")
print(f"  Страница: '{chosen['page_name']}' (page_id={chosen['page_id']})")
print(f"  Файл: {OUT_FILE}")
print("  (page_access_token и access_token записаны, в вывод не печатаются)")
