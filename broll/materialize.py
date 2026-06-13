"""Materialize-слой + валидация загрузок для Pipeline 2 (13 июня).

CTO-ревью Critical 2: единый слой ПЕРЕД `assemble_broll_montage` превращает
любой BrollItem в mp4, чтобы ассемблер по-прежнему получал только видео:
  - video / hf_scene — passthrough (ассемблер сам нормализует blurred-pad);
  - image — Ken Burns mp4 (переиспользуем video_assembler._build_ken_burns_clips).
Битый item пропускается (а не валит весь ролик).

+ `validate_upload_media`: загрузки проверяются ffprobe (размер/длительность/
битость), иначе 4K/HEVC станет главным bottleneck ffmpeg.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("broll.materialize")

MAX_UPLOAD_BYTES = 200 * 1024 * 1024   # 200 МБ на файл
MIN_VIDEO_SEC = 1.0
IMAGE_CLIP_SEC = 5.0                    # длина Ken Burns клипа из фото


def _default_image_converter(photo_path: str, out_dir: Path, seg_len: float) -> Path:
    """Фото → Ken Burns mp4 (1080×1920). Переиспользует video_assembler."""
    from video_assembler import _build_ken_burns_clips
    clips = _build_ken_burns_clips(
        [Path(photo_path)], Path(out_dir), seg_len,
        name_prefix=f"up_{Path(photo_path).stem}",
    )
    if not clips:
        raise RuntimeError(f"Ken Burns не сгенерил клип из {photo_path}")
    return clips[0]


def materialize_items(items, work_dir, seg_len: float = IMAGE_CLIP_SEC,
                      image_converter=None) -> list[Path]:
    """BrollItem[] → list[Path к mp4], в исходном порядке.

    Битый/непереводимый item логируется и пропускается (ролик собирается из
    оставшихся). image_converter инъектируем для тестов."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    conv = image_converter or _default_image_converter
    out: list[Path] = []
    for it in items:
        try:
            if it.kind in ("video", "hf_scene"):
                p = Path(it.path)
                if not str(p):
                    raise ValueError("пустой путь")
                out.append(p)
            elif it.kind == "image":
                out.append(Path(conv(it.path, work_dir, seg_len)))
            else:
                logger.warning(f"[materialize] неизвестный kind {it.kind!r} — пропуск")
        except Exception as e:
            logger.warning(f"[materialize] item пропущен ({it.kind} {it.path}): {e}")
    return out


def _ffprobe_media(path: Path) -> dict:
    """Минимальные метаданные через ffprobe. Бросает при битом файле."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,width,height",
         "-of", "default=nw=1", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffprobe failed")[:200])
    info: dict = {}
    for line in (r.stdout or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "duration":
            try:
                info["duration"] = float(v)
            except ValueError:
                pass
        elif k == "codec_name":
            info.setdefault("codec", v.strip())
        elif k in ("width", "height"):
            try:
                info[k] = int(v)
            except ValueError:
                pass
    return info


def validate_upload_media(path, kind: str, probe_fn=None,
                          size_override: int | None = None) -> tuple[bool, str]:
    """(ok, reason) для загруженного файла. kind: video | image.

    probe_fn инъектируем (тесты), по умолчанию ffprobe. Консервативно:
    битость/короткость/размер → отказ с человекочитаемой причиной."""
    path = Path(path)
    size = size_override if size_override is not None else (
        path.stat().st_size if path.exists() else 0)
    if size <= 0:
        return False, "файл пустой или не найден"
    if size > MAX_UPLOAD_BYTES:
        return False, f"файл слишком велик (>{MAX_UPLOAD_BYTES // (1024*1024)} МБ)"

    probe = probe_fn or _ffprobe_media
    try:
        info = probe(path)
    except Exception as e:
        return False, f"файл битый или не медиа (не удалось прочитать: {e})"

    if kind == "video":
        dur = info.get("duration")
        if dur is None:
            return False, "не удалось определить длительность видео"
        if dur < MIN_VIDEO_SEC:
            return False, f"видео слишком короткое (<{MIN_VIDEO_SEC:.0f}с)"
    elif kind == "image":
        if not (info.get("width") and info.get("height")):
            return False, "не удалось прочитать размеры картинки"
    return True, "ок"
