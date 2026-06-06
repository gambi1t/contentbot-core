"""selfie.punch_in — динамический punch-in монтаж для селфи-видео.

Минутное селфи с руки имеет статичную крупность → скучно смотреть. Решение —
нарезать на сегменты по границам предложений (из Whisper word-timestamps) и
применить разный зум (100/108/114%, лицо в кадре), создавая «интервью-эффект»
из одной камеры с жёсткими склейками.

Разделение:
- `plan_punch_in_segments` — ЧИСТАЯ логика расчёта сегментов (TDD, без ffmpeg).
- `render_punch_in` — ffmpeg-сборка (scale/crop/concat + якорь лица).
- `punch_in_enabled` — env-флаг включения.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Граница предложения: слово заканчивается на один из этих знаков.
_SENTENCE_END = (".", "!", "?", "…")
# Пауза между словами больше этого (сек) → тоже граница (Whisper не всегда
# ставит пунктуацию).
_PAUSE_THRESHOLD = 0.6


def punch_in_enabled() -> bool:
    """Включён ли punch-in монтаж (env SELFIE_PUNCH_IN=1). Дефолт — выкл."""
    return os.getenv("SELFIE_PUNCH_IN", "0") == "1"


def _raw_groups(words: list[dict]) -> list[list[dict]]:
    """Сгруппировать words в «предложения» по пунктуации + паузам."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        text = str(w.get("word", "")).rstrip()
        ends_sentence = text.endswith(_SENTENCE_END)
        # Пауза до следующего слова
        big_pause = False
        if i + 1 < len(words):
            gap = float(words[i + 1]["start"]) - float(w["end"])
            big_pause = gap > _PAUSE_THRESHOLD
        if ends_sentence or big_pause:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def _group_span(group: list[dict]) -> tuple[float, float]:
    return float(group[0]["start"]), float(group[-1]["end"])


def _split_long_group(group: list[dict], max_seg: float) -> list[list[dict]]:
    """Разбить слишком длинную группу на куски ≤ max_seg по word-границам."""
    start, end = _group_span(group)
    dur = end - start
    if dur <= max_seg:
        return [group]
    n_parts = int(dur // max_seg) + 1
    target = dur / n_parts
    parts: list[list[dict]] = []
    cur: list[dict] = []
    cur_start = start
    for w in group:
        cur.append(w)
        if float(w["end"]) - cur_start >= target and len(cur) > 0:
            parts.append(cur)
            cur = []
            cur_start = float(w["end"])
    if cur:
        parts.append(cur)
    return parts


def plan_punch_in_segments(
    words: list[dict],
    min_seg: float = 4.0,
    max_seg: float = 12.0,
    zoom_levels=(1.0, 1.08, 1.14),
    total_duration: float | None = None,
) -> list[dict]:
    """Расчёт сегментов для punch-in монтажа.

    Args:
        words: Whisper word-timestamps [{"word","start","end"}, ...].
        min_seg: минимальная длина сегмента (короче — склеиваем).
        max_seg: максимальная (длиннее — дробим).
        zoom_levels: уровни крупности; [0] — базовый (первый сегмент).
        total_duration: полная длительность ВИДЕО. Если задана — первый сегмент
            расширяется до 0.0, последний до total_duration. КРИТИЧНО для
            синхронизации: слова начинаются не с 0 (пауза) и кончаются раньше
            конца видео; без этого видео обрезается, а аудио (copy) полное →
            рассинхрон губ (баг 9 июня).

    Returns:
        [{"start": float, "end": float, "zoom": float}, ...] — непрерывный
        таймлайн без дыр (start[i+1] == end[i]). Соседние zoom различаются.
    """
    if not words:
        return []

    zoom_levels = tuple(zoom_levels) or (1.0,)

    # 1. Группировка по предложениям/паузам.
    groups = _raw_groups(words)

    # 2. Дробление длинных групп.
    split_groups: list[list[dict]] = []
    for g in groups:
        split_groups.extend(_split_long_group(g, max_seg))

    # 3. Склейка коротких групп до min_seg.
    merged: list[list[dict]] = []
    for g in split_groups:
        if merged:
            prev_start, prev_end = _group_span(merged[-1])
            if (prev_end - prev_start) < min_seg:
                merged[-1] = merged[-1] + g
                continue
        merged.append(g)
    # Если последний сегмент короче min_seg — приклеить к предыдущему.
    if len(merged) >= 2:
        last_start, last_end = _group_span(merged[-1])
        if (last_end - last_start) < min_seg:
            merged[-2] = merged[-2] + merged[-1]
            merged.pop()

    # 4. Назначение zoom — соседние различаются. Первый = базовый zoom_levels[0].
    segments: list[dict] = []
    prev_zoom = None
    alt_idx = 0  # курсор по не-базовым уровням
    non_base = [z for z in zoom_levels if z != zoom_levels[0]]
    for i, g in enumerate(merged):
        start, end = _group_span(g)
        if i == 0:
            zoom = zoom_levels[0]
        elif not non_base:
            zoom = zoom_levels[0]  # один уровень — всё базовое
        else:
            # Чередуем: если предыдущий был базовый → берём из non_base;
            # если был не-базовый → возвращаемся к базовому (динамика «туда-сюда»).
            if prev_zoom == zoom_levels[0]:
                zoom = non_base[alt_idx % len(non_base)]
                alt_idx += 1
            else:
                zoom = zoom_levels[0]
        segments.append({"start": start, "end": end, "zoom": zoom})
        prev_zoom = zoom

    # Покрыть весь ролик [0, total_duration] — иначе видео обрежется относительно
    # аудио (которое copy целиком) → рассинхрон. Слова задают точки РЕЗА, а не
    # границы ролика.
    if total_duration is not None and segments:
        segments[0]["start"] = 0.0
        segments[-1]["end"] = float(total_duration)

    return segments


# ── ffmpeg-рендер ────────────────────────────────────────────────────────────

def _even(n: float) -> int:
    """Округлить до ближайшего чётного (требование libx264/yuv420p)."""
    return int(round(n / 2.0)) * 2


def _probe_wh(video_path: Path) -> tuple[int, int] | None:
    """Реальные width×height входа через ffprobe."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        w_str, h_str = res.stdout.strip().split(",")[:2]
        return int(w_str), int(h_str)
    except Exception as e:
        logger.warning(f"[punch_in] ffprobe wh failed: {e}")
        return None


def _probe_duration(video_path: Path) -> float | None:
    """Длительность видео (сек) через ffprobe."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(res.stdout.strip())
    except Exception as e:
        logger.warning(f"[punch_in] ffprobe duration failed: {e}")
        return None


def _head_fraction(source: Path, tmp_dir: Path) -> float:
    """Вертикальная доля верха головы (0..1). Reuse OpenCV-детектора из
    video_assembler. Fallback 0.33 (лицо обычно в верхней трети селфи)."""
    try:
        from video_assembler import _detect_head_top_fraction
        frac = _detect_head_top_fraction(source, tmp_dir)
        if frac is not None:
            return max(0.0, min(1.0, frac))
    except Exception as e:
        logger.info(f"[punch_in] head-detect unavailable: {e}")
    return 0.33


def render_punch_in(source: Path, segments: list[dict], output: Path) -> Path:
    """Собрать punch-in видео: каждый сегмент масштабируется по своему zoom и
    обрезается обратно в исходный размер с вертикальным якорем на лицо.

    Длительность и аудио сохраняются (concat покрывает всю шкалу, голос copy).
    Возвращает output. Бросает RuntimeError при сбое (caller — fallback на source).
    """
    source = Path(source)
    output = Path(output)
    if not segments:
        raise RuntimeError("punch_in: пустой план сегментов")

    wh = _probe_wh(source)
    if not wh:
        raise RuntimeError("punch_in: не удалось определить размер видео")
    W, H = wh

    # Страховка от рассинхрона: видео ОБЯЗАНО покрыть [0, duration], иначе
    # обрежется относительно полного аудио (-c:a copy). Слова дают точки реза,
    # но первый сегмент тянем к 0, последний — к реальному концу видео.
    segments = [dict(s) for s in segments]  # копия, не мутируем вход
    duration = _probe_duration(source)
    segments[0]["start"] = 0.0
    if duration:
        segments[-1]["end"] = duration

    # Якорь лица — один раз на весь ролик (стабильная рамка).
    frac = _head_fraction(source, output.parent)

    parts = []
    labels = []
    for i, seg in enumerate(segments):
        z = float(seg["zoom"])
        s = float(seg["start"])
        e = float(seg["end"])
        if z <= 1.0001:
            # Без зума — просто обрезка по времени, размер исходный.
            parts.append(
                f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,"
                f"scale={W}:{H},setsar=1[v{i}]"
            )
        else:
            sw = _even(W * z)
            sh = _even(H * z)
            x_off = _even((sw - W) / 2.0)               # центр по горизонтали
            # Вертикаль: якорь на лицо. y_off = frac*(sh-H), clamp [0, sh-H].
            y_raw = frac * (sh - H)
            y_off = _even(max(0, min(sh - H, y_raw)))
            parts.append(
                f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,"
                f"scale={sw}:{sh},crop={W}:{H}:{x_off}:{y_off},setsar=1[v{i}]"
            )
        labels.append(f"[v{i}]")

    concat = "".join(labels) + f"concat=n={len(segments)}:v=1:a=0[outv]"
    filter_complex = ";".join(parts) + ";" + concat

    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(output),
    ]
    logger.info(
        f"[punch_in] rendering {len(segments)} segments "
        f"(W×H={W}×{H}, head_frac={frac:.2f})"
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output.exists():
        raise RuntimeError(
            f"punch_in ffmpeg failed (rc={result.returncode}): "
            f"{result.stderr[-400:]}"
        )
    return output
