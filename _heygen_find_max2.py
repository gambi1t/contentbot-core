"""Найти голос Max2 в HeyGen + проверить default_voice_id у аватара Максима.

Цель — собрать данные для решения: переходить ли на HeyGen-native голос
(text-mode), убрав звено ElevenLabs из пайплайна бота.

Запуск НА СЕРВЕРЕ:  python _heygen_find_max2.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).parent

MAKSIM_GROUP_ID = "c401ba1d61054d5aa297b937d864e5d3"


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
        # 1. Список голосов аккаунта — ищем Max / Maksim / Max2 / Юмсунов
        print("=== ПОИСК ГОЛОСА Max2 ===")
        r = c.get("https://api.heygen.com/v2/voices", headers=h)
        print(f"GET /v2/voices → HTTP {r.status_code}")
        try:
            d = r.json().get("data", {})
            voices = d.get("voices") or d.get("voice_list") or d.get("list") or []
            print(f"Всего голосов на аккаунте: {len(voices)}")
            hits = []
            for v in voices:
                if not isinstance(v, dict):
                    continue
                name = (v.get("name") or v.get("voice_name") or "").lower()
                if any(needle in name for needle in
                       ("max", "maksim", "юмсун", "yumsun")):
                    hits.append(v)
            print(f"\nСовпадений по Max/Maksim/Юмсунов: {len(hits)}")
            for v in hits:
                # Печатаем самые полезные поля
                useful = {k: v.get(k) for k in (
                    "voice_id", "name", "voice_name", "language",
                    "gender", "preview_audio", "source", "support_pause",
                    "emotion_support",
                ) if v.get(k) is not None}
                print(f"  {useful}")
        except Exception as e:
            print(f"  parse error: {e} — body: {r.text[:300]}")

        # 2. Полная запись аватара «Maksim Yumsunov» — есть ли у группы
        #    default_voice_id (если ты выставил Max2 как default в Studio).
        print(f"\n=== АВАТАР МАКСИМА ({MAKSIM_GROUP_ID}) ===")
        r = c.get("https://api.heygen.com/v2/avatar_group.list", headers=h)
        if r.status_code == 200:
            d = r.json().get("data", {})
            groups = d.get("avatar_group_list") or d.get("groups") or []
            mg = next((g for g in groups if g.get("id") == MAKSIM_GROUP_ID), None)
            if mg:
                useful = {k: v for k, v in mg.items() if k not in ("preview_image",)}
                for k, v in useful.items():
                    print(f"  {k}: {v}")
            else:
                print("  не найден среди групп")
        else:
            print(f"  HTTP {r.status_code}: {r.text[:200]}")

        # 3. Looks этой группы — у каждого look может быть свой default_voice
        print(f"\n=== LOOKS АВАТАРА МАКСИМА ===")
        # HeyGen v3 API: GET /v3/photo_avatar/{group_id} or list
        for url in [
            f"https://api.heygen.com/v2/avatar_group/{MAKSIM_GROUP_ID}/avatars",
            f"https://api.heygen.com/v2/photo_avatar/{MAKSIM_GROUP_ID}",
        ]:
            r = c.get(url, headers=h)
            print(f"GET {url} → HTTP {r.status_code}")
            if r.status_code == 200:
                try:
                    body = r.json()
                    d = body.get("data", body)
                    items = (
                        d.get("avatar_list") or d.get("photo_avatar_list")
                        or d.get("list") or d.get("avatars") or [d]
                    )
                    if not isinstance(items, list):
                        items = [items]
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        useful = {k: it.get(k) for k in (
                            "id", "avatar_id", "name", "status",
                            "default_voice_id", "supported_api_engines",
                        ) if it.get(k) is not None}
                        if useful:
                            print(f"  {useful}")
                    break
                except Exception as e:
                    print(f"  parse error: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
