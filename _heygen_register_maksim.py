"""Регистрация постоянного фото-аватара Максима в HeyGen.

POST /v3/avatars type:photo — даёт переиспользуемый avatar_id, который
можно гонять на дешёвом Avatar III.

Запуск НА СЕРВЕРЕ:  python _heygen_register_maksim.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
PHOTO = HERE / "_voice_test" / "max_1.jpg"


def main() -> int:
    from bot import heygen_register_photo_avatar, save_media_permanent

    if not PHOTO.exists():
        print(f"FAIL: нет фото {PHOTO}")
        return 1

    photo_url = save_media_permanent(str(PHOTO), prefix="maksim_avatar")
    print(f"photo_url: {photo_url}")

    avatar_id = heygen_register_photo_avatar(photo_url, "Maksim Yumsunov")
    print(f"\nЗАРЕГИСТРИРОВАН. avatar_id = {avatar_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
