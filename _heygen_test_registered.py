"""Проверка: генерит ли видео ЗАРЕГИСТРИРОВАННЫЙ фото-аватар Максима
через стандартный /v2/video/generate (а не Image-to-Video).

Если работает — аватар можно вшить в бренд maksim как heygen_avatar_id.

Запуск НА СЕРВЕРЕ:  python _heygen_test_registered.py
Выход: _voice_test/registered_avatar_test.mp4
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
AUDIO = HERE / "_voice_test" / "sample_v3_max2.mp3"
OUT = HERE / "_voice_test" / "registered_avatar_test.mp4"

# Зарегистрированный фото-аватар Максима (avatar_item.id)
MAKSIM_AVATAR_ID = "90610f1ac6c846aebced55f202e122e8"


def main() -> int:
    from bot import heygen_generate_video, heygen_check_status, HEYGEN_API_KEY

    if not AUDIO.exists():
        print(f"FAIL: нет аудио {AUDIO}")
        return 1

    # Аудио → HeyGen asset upload
    with open(AUDIO, "rb") as f:
        r = httpx.post(
            "https://upload.heygen.com/v1/asset",
            headers={"X-Api-Key": HEYGEN_API_KEY, "Content-Type": "audio/mpeg"},
            content=f.read(), timeout=120,
        )
    ud = r.json()
    if ud.get("code") != 100:
        print(f"FAIL: audio upload — {ud}")
        return 1
    audio_url = ud["data"]["url"]
    print(f"audio_url: {audio_url}")

    # Генерация: registered photo avatar id + Avatar IV (он только avatar_iv)
    try:
        video_id = heygen_generate_video(
            audio_url, look_id=MAKSIM_AVATAR_ID, avatar_version="v4",
        )
    except Exception as e:
        print(f"FAIL: heygen_generate_video — {e}")
        return 1
    print(f"video_id: {video_id}")

    for i in range(80):
        time.sleep(10)
        st = heygen_check_status(video_id)
        print(f"[{i}] status={st.get('status')}")
        if st.get("status") == "completed":
            resp = httpx.get(st["video_url"], timeout=180)
            resp.raise_for_status()
            OUT.write_bytes(resp.content)
            print(f"OK: {OUT} — {OUT.stat().st_size // 1024} KB")
            return 0
        if st.get("status") in ("failed", "error"):
            print(f"FAIL: HeyGen вернул {st}")
            return 1
    print("FAIL: таймаут")
    return 1


if __name__ == "__main__":
    sys.exit(main())
