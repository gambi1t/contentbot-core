"""Pilot для W2: 10-15 пробных кадров из Pexels + Pixabay → шлёт Артёму в Telegram.

Цель: оценить релевантность стоковых видео для talking-head бизнес-контента
Максима ДО массовой закачки. Если 5-10 кадров подходят — катимся в волну 2.
Если ни одного — отказываемся от стоков, смотрим только YouTube + AI.

Использование (на сервере под maksim-bot):
    python3 scripts/pilot_broll_fetch.py

Сохраняет в /srv/bot-media-maksim/test-pilot/<query>/<source>_<id>.mp4
и шлёт каждое видео Артёму (chat_id 384671843) с подписью "источник + query + duration".
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

# Подтянуть .env
ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
if ENV.exists():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

PEXELS_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ARTEM_CHAT_ID = 384671843  # из reference_nox_maksim_server.md allowlist

OUT_DIR = Path("/srv/bot-media-maksim/test-pilot")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 4 query — предметные, талкинг-хед бизнес-контекст, минимум людей
QUERIES = [
    "office desk closeup",          # документы, бумаги, ноутбук
    "laptop keyboard typing hands", # макро рук
    "coffee morning desk",          # утренняя предметка
    "notebook pen writing",         # рукопись/заметки
]
# По 2 видео с каждого источника на каждый query → ~16 клипов


def pexels_search(query: str, count: int = 3) -> list[dict]:
    if not PEXELS_KEY:
        return []
    try:
        r = httpx.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": count, "orientation": "portrait"},
            headers={"Authorization": PEXELS_KEY},
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"[pexels] {query!r} fail: {e}")
        return []
    out = []
    for v in r.json().get("videos", [])[:count]:
        files = sorted(
            [f for f in v.get("video_files", []) if 720 <= f.get("width", 0) <= 1920],
            key=lambda f: f.get("width", 0),
        )
        if not files:
            continue
        hd = files[0]
        out.append({
            "source": "pexels", "id": v.get("id"),
            "url": hd["link"], "width": hd.get("width"), "height": hd.get("height"),
            "duration": v.get("duration", 0), "user": v.get("user", {}).get("name", ""),
        })
    return out


def pixabay_search(query: str, count: int = 3) -> list[dict]:
    if not PIXABAY_KEY:
        return []
    try:
        r = httpx.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_KEY, "q": query, "per_page": count, "safesearch": "true"},
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"[pixabay] {query!r} fail: {e}")
        return []
    out = []
    for v in r.json().get("hits", [])[:count]:
        videos = v.get("videos", {})
        # medium/small obviously HD ~720p
        chosen = videos.get("medium") or videos.get("small")
        if not chosen or not chosen.get("url"):
            continue
        out.append({
            "source": "pixabay", "id": v.get("id"),
            "url": chosen["url"], "width": chosen.get("width"),
            "height": chosen.get("height"),
            "duration": v.get("duration", 0), "user": v.get("user", ""),
        })
    return out


def download(url: str, dest: Path, timeout: int = 60) -> bool:
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"[download] {dest.name} fail: {e}")
        return False


def tg_send_video(chat_id: int, path: Path, caption: str) -> dict:
    """Send video via Telegram Bot API. Returns response JSON."""
    if not TG_TOKEN:
        return {"ok": False, "error": "no TG token"}
    with open(path, "rb") as f:
        r = httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"video": (path.name, f, "video/mp4")},
            timeout=120,
        )
    return r.json()


def main():
    print(f"OUT_DIR: {OUT_DIR}")
    print(f"Pexels key: {'YES' if PEXELS_KEY else 'NO'} | Pixabay key: {'YES' if PIXABAY_KEY else 'NO'} | TG: {'YES' if TG_TOKEN else 'NO'}")
    print(f"Artem chat_id: {ARTEM_CHAT_ID}")
    print()

    # Header
    if TG_TOKEN:
        httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={
                "chat_id": ARTEM_CHAT_ID,
                "text": (
                    "🧪 *W2 pilot* — 16 пробных кадров из Pexels + Pixabay "
                    "под 4 query (предметка для talking-head).\n\n"
                    "Оцени каждый: подходит / не подходит / переснять. "
                    "Если общая релевантность <30% — стоки пропускаем, идём в YouTube/AI."
                ),
                "parse_mode": "Markdown",
            },
            timeout=20,
        )

    total_sent = 0
    for query in QUERIES:
        q_dir = OUT_DIR / query.replace(" ", "_")
        q_dir.mkdir(exist_ok=True)
        if TG_TOKEN:
            httpx.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data={
                    "chat_id": ARTEM_CHAT_ID,
                    "text": f"━━━━━━━━━━\n📁 *Query:* `{query}`",
                    "parse_mode": "Markdown",
                },
                timeout=20,
            )
        for searcher_name, searcher in [("pexels", pexels_search), ("pixabay", pixabay_search)]:
            results = searcher(query, count=2)
            print(f"[{searcher_name}] {query!r}: {len(results)} videos")
            for v in results:
                fname = f"{v['source']}_{v['id']}.mp4"
                dest = q_dir / fname
                if not dest.exists():
                    if not download(v["url"], dest):
                        continue
                caption = (
                    f"{v['source']}/{v['id']} · {query}\n"
                    f"{v.get('width')}x{v.get('height')} · {v.get('duration')}s · @{v.get('user', '?')}"
                )
                if TG_TOKEN:
                    resp = tg_send_video(ARTEM_CHAT_ID, dest, caption)
                    if resp.get("ok"):
                        total_sent += 1
                        print(f"  sent {fname}")
                    else:
                        print(f"  send fail {fname}: {resp.get('description', resp)}")
                else:
                    total_sent += 1

    # Footer
    if TG_TOKEN:
        httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={
                "chat_id": ARTEM_CHAT_ID,
                "text": (
                    f"━━━━━━━━━━\n✅ *Пилот завершён.* Отправлено: {total_sent} клипов.\n\n"
                    "Дай обратную связь: какие подходят / не подходят / нужно других "
                    "queries / стоки в принципе не годятся."
                ),
                "parse_mode": "Markdown",
            },
            timeout=20,
        )
    print(f"\nTotal sent: {total_sent}")


if __name__ == "__main__":
    main()
