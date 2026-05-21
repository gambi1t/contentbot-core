"""Тест: оживить фото Максима (HeyGen Image-to-Video) + клонированный голос.

Берём max_1.JPG (лицо открыто) и аудио sample_v3_max2.mp3 (голос Максима,
клон v3) → HeyGen Image-to-Video (Avatar IV) → говорящее видео.

Запуск НА СЕРВЕРЕ:  python _gen_photo_avatar_test.py
Выход: _voice_test/photo_avatar_test.mp4
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
PHOTO = HERE / "_voice_test" / "max_1.jpg"
AUDIO = HERE / "_voice_test" / "sample_v3_max2.mp3"
OUT = HERE / "_voice_test" / "photo_avatar_test.mp4"


def main() -> int:
    from bot import (
        heygen_v3_image_to_video,
        heygen_v3_check_status,
        save_media_permanent,
        HEYGEN_API_KEY,
    )
    # heygen_test_handlers держит ключ в своём модульном глобале, который
    # ставится только в register_heygen_test_handlers() (при wire-up бота).
    # В standalone-скрипте проставляем напрямую.
    import heygen_test_handlers as _hth
    _hth._heygen_api_key = HEYGEN_API_KEY

    if not PHOTO.exists():
        print(f"FAIL: нет фото {PHOTO}")
        return 1
    if not AUDIO.exists():
        print(f"FAIL: нет аудио {AUDIO}")
        return 1

    # 1. Фото → публичный URL (HeyGen скачает его)
    photo_url = save_media_permanent(str(PHOTO), prefix="maksim_face")
    print(f"photo_url: {photo_url}")

    # 2. Аудио → HeyGen asset upload (raw binary)
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

    # 3. Image-to-Video (Avatar IV анимирует произвольное фото)
    video_id = heygen_v3_image_to_video(photo_url, audio_url, "v4")
    print(f"video_id: {video_id}")

    # 4. Ожидание
    for i in range(80):
        time.sleep(10)
        st = heygen_v3_check_status(video_id)
        print(f"[{i}] status={st.get('status')}")
        if st.get("status") == "completed":
            url = st.get("video_url")
            resp = httpx.get(url, timeout=180)
            resp.raise_for_status()
            OUT.write_bytes(resp.content)
            print(f"OK: {OUT} — {OUT.stat().st_size // 1024} KB")
            return 0
        if st.get("status") in ("failed", "error"):
            print(f"FAIL: HeyGen вернул {st}")
            return 1
    print("FAIL: таймаут ожидания HeyGen (>13 мин)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
