"""Проверка: generate_audio=false → клип БЕЗ аудио-дорожки (1 клип, ~$0.56)."""
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fal_media  # noqa: E402

p = fal_media.generate_kling_video(
    "Multiple shots. [wide shot] a calm empty city street at dawn, slow dolly forward, premium light, cinematic",
    "/tmp/kling_audiotest.mp4", duration=5)
print("path:", p)
if p:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type", "-of", "csv=p=0", p],
                         capture_output=True, text=True)
    streams = out.stdout.strip().split("\n")
    print("streams:", streams)
    print("AUDIO:", "ЕСТЬ (плохо)" if "audio" in streams else "НЕТ — generate_audio=false работает ✓")
