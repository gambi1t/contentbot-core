"""HeyGen аватар-видео для теста монтажа: look «Белая футболка» + озвучка.

Запускать НА СЕРВЕРЕ (HeyGen блокирует РФ-IP, как и ElevenLabs).
Вход:  _montage_test/voiceover.mp3
Выход: _montage_test/avatar_01.mp4
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

OUT = Path(__file__).parent / "_montage_test"
# HEYGEN_LOOKS["look2"] — «Белая футболка» (look Артёма)
LOOK_BELAYA_FUTBOLKA = "9a3fa1911a2a43fbbd428fd186a254bf"


def main() -> int:
    from bot import heygen_generate_video, heygen_check_status, save_media_permanent

    voiceover = OUT / "voiceover.mp3"
    if not voiceover.exists():
        print(f"FAIL: нет {voiceover}")
        return 1

    audio_url = save_media_permanent(str(voiceover), prefix="montage_audio")
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
    print(f"OK: avatar_01.mp4 — {out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
