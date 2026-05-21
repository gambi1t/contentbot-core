"""Чистка фото-аватар-групп HeyGen — удаляем мусор «Avatar IV Video»
и старые тестовые, оставляем только нужное.

Запуск НА СЕРВЕРЕ:  python _heygen_cleanup_avatars.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

HERE = Path(__file__).parent

# НЕ удалять — оставляем:
KEEP_IDS = {
    "92c9d7c47f374c2f8542a927417b9deb",  # Custom Photo Avatar — нужен для обувки
    "b3c4e903bc024c76828209d75d55692f",  # Kirill — придержан до подтверждения Артёма
}


def _key() -> str:
    for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("HEYGEN_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    key = _key()
    if not key:
        print("FAIL: нет HEYGEN_API_KEY")
        return 1
    h = {"x-api-key": key, "Accept": "application/json"}

    with httpx.Client(timeout=30) as c:
        r = c.get("https://api.heygen.com/v2/avatar_group.list", headers=h)
        d = r.json().get("data", {})
        groups = d.get("avatar_group_list") or d.get("groups") or d.get("list") or []
        to_delete = [g for g in groups if g.get("id") not in KEEP_IDS]
        kept = [g for g in groups if g.get("id") in KEEP_IDS]
        print(f"Всего групп: {len(groups)} | оставляем: {len(kept)} | удаляем: {len(to_delete)}")
        for g in kept:
            print(f"  KEEP: {g.get('id')} {g.get('name')!r}")
        if not to_delete:
            print("Нечего удалять.")
            return 0

        ok = fail = 0
        for i, g in enumerate(to_delete):
            gid = g.get("id")
            resp = c.delete(
                f"https://api.heygen.com/v2/photo_avatar_group/{gid}", headers=h
            )
            try:
                code = resp.json().get("code")
            except Exception:
                code = None
            good = resp.status_code == 200 and code == 100
            ok += good
            fail += not good
            tag = "OK" if good else f"FAIL: {resp.text[:150]}"
            print(f"  [{i+1}/{len(to_delete)}] {gid} {g.get('name')!r} "
                  f"→ HTTP {resp.status_code} code={code} {tag}")
            # Тест на одном: если первое удаление не прошло — стоп.
            if i == 0 and not good:
                print("Первое удаление не прошло — стоп. Проверь эндпоинт/ключ.")
                return 1

        print(f"\nИтог: удалено {ok}, ошибок {fail}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
