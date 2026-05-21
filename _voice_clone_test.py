"""Клон голоса Максима — ТОЛЬКО из max2.MP3 (чистый образец), сэмпл на v3.

Запуск НА СЕРВЕРЕ (ElevenLabs блокирует РФ-IP).
Вход:  _voice_test/max2.MP3
Выход: _voice_test/sample_v3.mp3 + voice_id в stdout.

Старый клон из двух файлов удаляется, чтобы не занимал слот аккаунта.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

BASE = "https://api.elevenlabs.io/v1"
HERE = Path(__file__).parent
SAMPLES_DIR = HERE / "_voice_test"

# Прежний клон (из max.MP3 + max2.MP3) — удалить.
OLD_VOICE_ID = "1aKMUabdzRf786XuWAD8"

TEST_LINE = (
    "Привет, я Максим Юмсунов. Это мой канал про реальный бизнес — "
    "без воды и красивых обещаний. Только то, что правда работает в деле."
)


def _key() -> str:
    k = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not k:
        env = HERE / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("ELEVENLABS_API_KEY="):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return k


def main() -> int:
    key = _key()
    if not key:
        print("FAIL: нет ELEVENLABS_API_KEY")
        return 1
    headers = {"xi-api-key": key}

    sample = SAMPLES_DIR / "max2.MP3"
    if not sample.exists():
        print(f"FAIL: нет образца {sample}")
        return 1
    print(f"Образец для клона: {sample.name} ({sample.stat().st_size // 1024} KB)")

    with httpx.Client(timeout=240) as c:
        # 0. Удаляем прежний клон из двух файлов
        if OLD_VOICE_ID:
            r = c.delete(f"{BASE}/voices/{OLD_VOICE_ID}", headers=headers)
            print(f"Удаление старого клона {OLD_VOICE_ID}: HTTP {r.status_code}")

        # 1. Клон ТОЛЬКО из max2.MP3
        files = [("files", (sample.name, sample.read_bytes(), "audio/mpeg"))]
        r = c.post(
            f"{BASE}/voices/add", headers=headers,
            data={"name": "Maksim Yumsunov"}, files=files,
        )
        if r.status_code != 200:
            print(f"FAIL: voices/add {r.status_code} — {r.text[:300]}")
            return 1
        voice_id = r.json()["voice_id"]
        print(f"VOICE_ID (клон из max2.MP3): {voice_id}")

        # 2. Сэмпл на eleven_v3
        r = c.post(
            f"{BASE}/text-to-speech/{voice_id}", headers=headers,
            json={"text": TEST_LINE, "model_id": "eleven_v3"},
        )
        if r.status_code != 200:
            print(f"FAIL: TTS v3 {r.status_code} — {r.text[:250]}")
            return 1
        out = SAMPLES_DIR / "sample_v3_max2.mp3"
        out.write_bytes(r.content)
        print(f"OK v3: {out.name} — {out.stat().st_size // 1024} KB")

    print(f"\nГотово. VOICE_ID={voice_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
