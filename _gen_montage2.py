"""Ролик #2 «Дорогие пустые часы»: озвучка + HeyGen-аватар одним прогоном.

Запускать НА СЕРВЕРЕ (ElevenLabs и HeyGen блокируют РФ-IP).
Выход: _montage_test2/voiceover.mp3 + _montage_test2/avatar_01.mp4
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

OUT = Path(__file__).parent / "_montage_test2"
OUT.mkdir(exist_ok=True)

# look «Белая футболка» (тот же, что в ролике #1)
LOOK_BELAYA_FUTBOLKA = "9a3fa1911a2a43fbbd428fd186a254bf"

SCRIPT = (
    "Самые дорогие часы в моём бизнесе — когда трасса пустая.\n\n"
    "В выходные у нас очередь. А во вторник днём — два человека. "
    "Я смотрел только на выходные и был спокоен.\n\n"
    "Но аренда, зарплаты и свет идут все семь дней. "
    "Пустой будний день — это не ноль. Это минус.\n\n"
    "Я считал не то. Не выручку выходных — загрузку за неделю.\n\n"
    "Выходные выжимать перестал — там и так очередь. Занялся буднями: "
    "корпоративы, дневной тариф, автошколы. Расписание начало закрываться.\n\n"
    "Как — в канале «Юмсунов про реальный бизнес»."
)


def main() -> int:
    from bot import (
        generate_voiceover,
        heygen_generate_video,
        heygen_check_status,
        save_media_permanent,
    )

    # ── 1. Озвучка ──
    voiceover = OUT / "voiceover.mp3"
    print("генерирую озвучку…")
    generate_voiceover(SCRIPT, str(voiceover))
    if not voiceover.exists() or voiceover.stat().st_size < 1000:
        print("FAIL: озвучка не создалась")
        return 1

    import json
    import subprocess

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(voiceover)],
        capture_output=True, text=True,
    )
    dur = float(json.loads(probe.stdout)["format"]["duration"])
    print(f"OK озвучка: {voiceover.stat().st_size // 1024} KB, {dur:.1f} сек")

    # ── 2. Аватар HeyGen ──
    audio_url = save_media_permanent(str(voiceover), prefix="montage2_audio")
    print(f"audio_url: {audio_url}")

    video_id = heygen_generate_video(
        audio_url, look_id=LOOK_BELAYA_FUTBOLKA, avatar_version="v3",
    )
    print(f"video_id: {video_id}")

    video_url = None
    for i in range(80):
        time.sleep(10)
        st = heygen_check_status(video_id)
        print(f"[{i}] status={st.get('status')}")
        if st.get("status") == "completed":
            video_url = st.get("video_url")
            break
        if st.get("status") in ("failed", "error"):
            print(f"FAIL: HeyGen вернул {st}")
            return 1
    if not video_url:
        print("FAIL: таймаут ожидания HeyGen (>13 мин)")
        return 1

    import httpx

    r = httpx.get(video_url, timeout=180)
    r.raise_for_status()
    out = OUT / "avatar_01.mp4"
    out.write_bytes(r.content)
    print(f"OK avatar: avatar_01.mp4 — {out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
