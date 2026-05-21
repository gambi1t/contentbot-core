"""Smoke-тест broll.assembler.assemble_broll_montage.

Берёт 6 реальных клипов из локального server-mirror архива, генерит
тестовую озвучку (sine ~24s через ffmpeg — содержимое аудио ассемблеру
безразлично), собирает монтаж и проверяет результат:
  - файл существует и не пустой
  - длина ≈ длине озвучки (±1.0s)
  - разрешение ровно 1080×1920
  - есть видео- и аудиодорожка

Запуск:  python _smoke_test_broll_assembler.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from broll.assembler import assemble_broll_montage, _probe_duration

BASE = Path(__file__).parent
ARCHIVE = BASE / "clips-to-upload" / "_server_mirror" / "clips" / "maksim"
VOICE_DUR = 24.0


def _pick_clips(n: int = 6) -> list[Path]:
    """6 клипов из разных категорий — проверяем смешанный aspect."""
    picks: list[Path] = []
    for cat in ("karting", "glamping", "sup", "personal"):
        cat_dir = ARCHIVE / cat
        if not cat_dir.exists():
            continue
        for mov in sorted(cat_dir.glob("*.mov"))[:2]:
            picks.append(mov)
            if len(picks) >= n:
                return picks
    return picks


def _probe_resolution(path: Path) -> tuple[int, int]:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    s = json.loads(res.stdout)["streams"][0]
    return s["width"], s["height"]


def _has_audio(path: Path) -> bool:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    return bool(json.loads(res.stdout).get("streams"))


def main() -> int:
    clips = _pick_clips(6)
    if len(clips) < 3:
        print(f"FAIL: мало клипов в архиве ({len(clips)}) — {ARCHIVE}")
        return 1
    print(f"клипы ({len(clips)}):")
    for c in clips:
        print(f"  {c.relative_to(ARCHIVE)}")

    tmp = Path(tempfile.mkdtemp(prefix="broll_smoke_"))
    voice = tmp / "test_voice.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=320:duration={VOICE_DUR}", "-c:a", "aac", str(voice)],
        capture_output=True, check=True,
    )
    print(f"тестовая озвучка: {VOICE_DUR}s")

    out = tmp / "montage.mp4"
    assemble_broll_montage(clips, voice, out, tmp_dir=tmp)

    # ── Проверки ──
    ok = True
    if not out.exists() or out.stat().st_size < 10_000:
        print(f"FAIL: выходной файл пустой/отсутствует: {out}")
        return 1

    dur = _probe_duration(out)
    if abs(dur - VOICE_DUR) > 1.0:
        print(f"FAIL: длина {dur:.2f}s, ожидалось ~{VOICE_DUR}s (±1.0)")
        ok = False
    else:
        print(f"OK: длина {dur:.2f}s ≈ {VOICE_DUR}s")

    w, h = _probe_resolution(out)
    if (w, h) != (1080, 1920):
        print(f"FAIL: разрешение {w}×{h}, ожидалось 1080×1920")
        ok = False
    else:
        print(f"OK: разрешение {w}×{h}")

    if not _has_audio(out):
        print("FAIL: нет аудиодорожки")
        ok = False
    else:
        print("OK: аудиодорожка на месте")

    size_mb = out.stat().st_size / 1024 / 1024
    print(f"размер: {size_mb:.1f} MB → {out}")

    print("\n" + ("✅ SMOKE PASS" if ok else "❌ SMOKE FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
