"""Комбайн B-roll: длинный YouTube-влог → нарезка по ПЛАНАМ (scene-detection)
→ короткие клипы в библиотеку → (затем) tag_clips.py для метаданных.

Разовая операция (запускается на сервере вручную). Переиспользует
webshare_proxy (анонимная скачка с ретраем по прокси) и структуру
broll-library, из которой потом берёт broll/selector.py.

Почему scene-detection, а не «по 5 сек»: влог уже смонтирован в планы —
мы их восстанавливаем; каждый клип = чистый план, без обрезков на стыках.

Почему анонимно (без куки): куки аккаунта через прокси YouTube флагует
(only-images) + рискует флагнуть Google-аккаунт. Публичный влог не требует куки.

CLI:
    python harvest_broll.py <youtube_url> [--scene travel] [--subscene winter]
        [--threshold 0.4] [--min 1.5] [--max 6.0] [--cap N]

Тесты чистой логики: python tests/test_harvest_broll.py
"""
from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("harvest_broll")

# YouTube прогрессив max 360p → HD только DASH (bestvideo+bestaudio merge).
HD_FORMAT = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
DEFAULT_THRESHOLD = 0.4
DEFAULT_MIN_LEN = 1.5
DEFAULT_MAX_LEN = 6.0
DOWNLOAD_RETRIES = 6


# ── Чистая логика (TDD) ───────────────────────────────────────────────────────

def _parse_scene_timestamps(ffmpeg_stderr: str) -> list[float]:
    """Вытащить pts_time из вывода ffmpeg showinfo (по порядку появления)."""
    return [float(m) for m in re.findall(r"pts_time:([0-9]+\.?[0-9]*)", ffmpeg_stderr or "")]


def _segments_from_scenes(
    scene_ts: list[float],
    duration: float,
    min_len: float = DEFAULT_MIN_LEN,
    max_len: float = DEFAULT_MAX_LEN,
) -> list[tuple[float, float]]:
    """Из таймкодов смены кадра построить сегменты (start, len).

    ОДИН клип на план: от начала плана, длиной min(длина_плана, max_len).
    Планы короче min_len выкидываются. Таймкоды вне (0, duration)
    игнорируются; вход сортируется и дедуплицируется.
    """
    if duration <= 0:
        return []
    inner = sorted({round(t, 3) for t in scene_ts if 0.0 < t < duration})
    boundaries = [0.0] + inner + [float(duration)]
    out: list[tuple[float, float]] = []
    for a, b in zip(boundaries, boundaries[1:]):
        shot_len = b - a
        if shot_len < min_len:
            continue
        out.append((round(a, 2), round(min(shot_len, max_len), 2)))
    return out


# ── Интеграция (ffmpeg / yt-dlp / библиотека) ──────────────────────────────────

def _probe_duration(src: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, timeout=30)
    return float((r.stdout or "0").strip() or 0)


def _detect_scenes(src: Path, threshold: float = DEFAULT_THRESHOLD) -> list[float]:
    """ffmpeg scene-детектор → таймкоды смены кадра."""
    r = subprocess.run(
        ["ffmpeg", "-i", str(src), "-filter:v",
         f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800)
    return _parse_scene_timestamps(r.stderr)


def _yt_dlp_bin() -> str:
    venv = Path(sys.executable).parent / "yt-dlp"
    return str(venv) if venv.exists() else "yt-dlp"


def _download_anon_hd(url: str, out: Path, retries: int = DOWNLOAD_RETRIES) -> bool:
    """Анонимная HD-скачка (DASH merge) с ретраем по прокси.

    YouTube челленджит IP неравномерно → перебираем прокси, пока не попадётся
    рабочий. Без куки (публичное видео не требует, куки только вредят)."""
    try:
        from webshare_proxy import get_random_proxy
    except Exception:
        get_random_proxy = lambda: None  # noqa: E731

    for attempt in range(1, retries + 1):
        if out.exists():
            out.unlink()
        cmd = [_yt_dlp_bin(), "-f", HD_FORMAT, "--merge-output-format", "mp4",
               "--max-filesize", "500M", "-o", str(out), "--no-playlist",
               "--remote-components", "ejs:github"]
        proxy = None
        try:
            proxy = get_random_proxy()
        except Exception:
            pass
        if proxy:
            cmd += ["--proxy", proxy]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if out.exists() and out.stat().st_size > 0:
            logger.info(f"[harvest] downloaded on attempt {attempt}")
            return True
        logger.warning(f"[harvest] download attempt {attempt}/{retries} failed: "
                       f"{(r.stderr or '')[-160:]}")
    return False


def _cut_segments(src: Path, segments: list[tuple[float, float]],
                  out_dir: Path, prefix: str = "vlog") -> list[Path]:
    """Нарезать сегменты в out_dir (re-encode для точного реза, без аудио,
    нормализация по высоте 720)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, (start, length) in enumerate(segments, 1):
        clip = out_dir / f"{prefix}_{i:03d}.mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(src),
             "-t", f"{length:.2f}", "-c:v", "libx264", "-an",
             "-vf", "scale=-2:720", "-preset", "veryfast", str(clip)],
            capture_output=True, text=True, timeout=180)
        if clip.exists() and clip.stat().st_size > 0:
            paths.append(clip)
        else:
            logger.warning(f"[harvest] cut failed {clip.name}: {(r.stderr or '')[-120:]}")
    return paths


def _library_dir(scene: str, subscene: str | None) -> Path:
    """broll-library/clips/maksim/<scene>[/<subscene>] — корень, из которого
    читает broll/selector.py."""
    from paths import LIBRARY_CLIPS_DIR
    d = Path(LIBRARY_CLIPS_DIR) / "maksim" / scene
    if subscene:
        d = d / subscene
    return d


def harvest(url: str, scene: str = "travel", subscene: str | None = "winter",
            threshold: float = DEFAULT_THRESHOLD, min_len: float = DEFAULT_MIN_LEN,
            max_len: float = DEFAULT_MAX_LEN, cap: int | None = None,
            work: Path | None = None) -> list[Path]:
    """Полный проход: скачать → детект планов → сегменты → нарезать в библиотеку.
    Возвращает пути нарезанных клипов. Теггинг (tag_clips.py) — отдельным шагом."""
    work = work or Path("/tmp/harvest_broll")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "source.mp4"

    print(f"[1/4] download {url} (anon + HD + proxy-retry)…")
    if not _download_anon_hd(url, src):
        print("DOWNLOAD_FAILED — YouTube заблокировал все попытки (повтори позже)")
        return []
    dur = _probe_duration(src)
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src)],
                         capture_output=True, text=True).stdout.strip()
    print(f"      got {res} dur={dur:.0f}s")

    print(f"[2/4] scene-detect (threshold={threshold})…")
    scenes = _detect_scenes(src, threshold)
    segs = _segments_from_scenes(scenes, dur, min_len, max_len)
    if cap:
        segs = segs[:cap]
    print(f"      {len(scenes)} смен кадра → {len(segs)} сегментов (после подрезки)")

    out_dir = _library_dir(scene, subscene)
    print(f"[3/4] нарезка в {out_dir}…")
    clips = _cut_segments(src, segs, out_dir, prefix=f"{scene}_{subscene or 'x'}")
    print(f"      нарезано {len(clips)} клипов")

    print("[4/4] следующий шаг: тегинг метаданных")
    print(f"      запусти: python tag_clips.py {out_dir}")
    return clips


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="B-roll харвест влога по планам")
    ap.add_argument("url")
    ap.add_argument("--scene", default="travel")
    ap.add_argument("--subscene", default="winter")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--min", type=float, default=DEFAULT_MIN_LEN, dest="min_len")
    ap.add_argument("--max", type=float, default=DEFAULT_MAX_LEN, dest="max_len")
    ap.add_argument("--cap", type=int, default=None, help="макс. число клипов")
    a = ap.parse_args()
    clips = harvest(a.url, a.scene, a.subscene, a.threshold, a.min_len, a.max_len, a.cap)
    print(f"\nГотово: {len(clips)} клипов в библиотеке.")
    return 0 if clips else 1


if __name__ == "__main__":
    sys.exit(main())
