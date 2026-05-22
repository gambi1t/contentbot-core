"""TDD-смоук фичи «свой голос»: произвольное аудио → HeyGen аватар.

Проверяет, что путь «загрузить готовое аудио (НЕ TTS) → сгенерить аватар»
работает с НОВЫМ ключом Максима и НОВЫМ видео-аватаром (Avatar IV).
Это фундамент фичи: бот уже умеет audio→avatar для TTS; здесь подтверждаем,
что произвольное аудио (как будущее голосовое Максима) проходит так же.

Запуск:  HEYGEN_KEY=... python _smoke_own_voice_avatar.py
"""
import os, sys, time, subprocess
import httpx

KEY = os.environ["HEYGEN_KEY"]
AVATAR = "a0bddf71c30c42aaa4cf4e4143039628"  # «Бежевый свитер — природа»
SRC = "_video_analysis/audio.mp3"
CLIP = "_smoke_own_voice_6s.mp3"
H = {"X-Api-Key": KEY}


def _ffmpeg() -> str:
    for p in ("ffmpeg", r"C:/ffmpeg/ffmpeg.exe", r"C:\ffmpeg\ffmpeg.exe"):
        try:
            subprocess.run([p, "-version"], capture_output=True, timeout=10)
            return p
        except Exception:
            continue
    return "ffmpeg"


def main() -> int:
    # 1) короткий клип (6с) — дёшево
    subprocess.run([_ffmpeg(), "-y", "-i", SRC, "-t", "6", "-c:a", "libmp3lame", CLIP],
                   capture_output=True)
    if not os.path.exists(CLIP):
        print("FAIL: ffmpeg не сделал клип"); return 1

    # 2) upload аудио-ассет (как делает бот: upload.heygen.com/v1/asset)
    with open(CLIP, "rb") as f:
        up = httpx.post("https://upload.heygen.com/v1/asset",
                        headers={"X-Api-Key": KEY, "Content-Type": "audio/mpeg"},
                        content=f.read(), timeout=120).json()
    print("upload code:", up.get("code"))
    if up.get("code") != 100:
        print("FAIL upload:", up); return 1
    audio_url = up["data"]["url"]
    print("audio_url ok:", audio_url[:60], "...")

    # 3) generate (avatar_iv, новый видео-аватар, voice.type=audio) — как бот
    payload = {
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": AVATAR,
                          "avatar_style": "normal", "use_avatar_iv_model": True},
            "voice": {"type": "audio", "audio_url": audio_url},
        }],
        "dimension": {"width": 1080, "height": 1920},
    }
    g = httpx.post("https://api.heygen.com/v2/video/generate",
                   headers={"X-Api-Key": KEY, "Content-Type": "application/json"},
                   json=payload, timeout=30).json()
    if g.get("error"):
        print("FAIL generate:", g["error"]); return 1
    vid = g["data"]["video_id"]
    print("video_id:", vid)

    # 4) poll
    for i in range(50):  # ~8 мин
        time.sleep(10)
        st = httpx.get(f"https://api.heygen.com/v1/video_status.get?video_id={vid}",
                       headers=H, timeout=30).json()
        s = st.get("data", {}).get("status")
        print(f"  [{i}] status: {s}")
        if s == "completed":
            url = st["data"]["video_url"]
            dur = st["data"].get("duration", "?")
            print(f"✅ DONE: duration={dur}s url={url[:80]}...")
            return 0
        if s == "failed":
            print("❌ FAILED:", st["data"].get("error")); return 1
    print("❌ TIMEOUT"); return 1


if __name__ == "__main__":
    sys.exit(main())
