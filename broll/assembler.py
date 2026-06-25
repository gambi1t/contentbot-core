"""Avatar-free B-roll montage assembler — Pipeline #2.

Собирает вертикальный ролик 1080×1920 из набора B-roll клипов под
закадровую озвучку. Никакой говорящей головы / HeyGen-аватара —
в отличие от video_assembler.py, где все функции требуют avatar_*.mp4
базовым слоем.

Поток:
  1. probe длины озвучки → целевая длина ролика
  2. каждый клип нормализуется в 1080×1920 (blurred-pad: клип вписан
     целиком + размытый увеличенный фон — ничего не обрезается, годится
     для смешанного материала верт./гориз.), обрезается до seg-длины,
     звук клипа выкидывается. Нормализация клипов идёт ПАРАЛЛЕЛЬНО
     (ThreadPoolExecutor) — это главный пожиратель времени.
  3. нормализованные сегменты склеиваются по кругу, пока сумма не покроет
     длину озвучки (или ПО ПОРЯДКУ без кругов при narrative=True — AI-видео)
  4. финал: видео обрезается точно по озвучке, озвучка муксится как
     аудиодорожка (опц. — тихий музыкальный бэк)

Субтитры здесь НЕ накладываются — это отдельный шаг
(subtitle_burner.add_subtitles_to_video) уже после сборки.

Производительность: blur считается на уменьшенной копии фона (270×480)
и масштабируется обратно — на порядок дешевле, чем boxblur по 1080×1920.
Промежуточные сегменты кодируются preset=ultrafast (их качество не важно,
финал всё равно перекодируется).
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger("broll.assembler")

CANVAS_W = 1080
CANVAS_H = 1920
FPS = 30

# Каждый клип в монтаже держим в этих границах. Длиннее MAX — обрезаем
# (длинный статичный клип усыпляет), короче MIN — всё равно берём, но
# такие клипы обычно отсеивает selector.
MAX_SEG_SEC = 5.0
MIN_SEG_SEC = 1.0
MAX_NARRATIVE_SEG_SEC = 15.0   # narrative (AI-видео): клип целиком, но safety-кап против runaway (Seedance max 12с)

# Параллелизм нормализации. Сервер nox-maksim — 4 ядра.
NORMALIZE_WORKERS = 4

# Громкость музыкального бэка относительно озвучки (если music передан).
MUSIC_VOLUME = 0.1259   # -18 dB, как эталон music_mixer.py (было 0.18=-15dB → музыка глушила голос)


class MontageError(Exception):
    """Сборка B-roll монтажа упала."""


def _run(cmd: list[str], desc: str, timeout: int = 600) -> str:
    """Запустить subprocess; MontageError при ненулевом коде. Возвращает stdout."""
    logger.info(f"[broll.assembler] {desc}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or "")[-800:]
        logger.error(f"[broll.assembler] {desc} failed:\n{tail}")
        raise MontageError(f"{desc} failed: {tail}")
    return result.stdout


def _probe_duration(media_path: Path) -> float:
    """Длительность медиа в секундах через ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(media_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise MontageError(
            f"ffprobe failed for {media_path}: {(result.stderr or '')[-400:]}"
        )
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise MontageError(f"ffprobe: нет duration у {media_path}: {e}")


def _normalize_clip(src: Path, seg_len: float, dst: Path) -> None:
    """Нормализовать один клип в 1080×1920, обрезать до seg_len, без звука.

    blurred-pad: фон — размытая увеличенная копия клипа, сам клип вписан
    целиком по центру. Ничего не кропается → подходит и для горизонтальных,
    и для вертикальных исходников.

    Оптимизация: blur делается на уменьшенной копии (270×480) и
    масштабируется обратно до 1080×1920. boxblur по полному кадру в разы
    дороже; на маленькой картинке размытие почти бесплатно, а растяжение
    обратно делает его даже мягче. preset=ultrafast — сегмент промежуточный.
    """
    vf = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale=270:480:force_original_aspect_ratio=increase,"
        f"crop=270:480,boxblur=8:1,"
        f"scale={CANVAS_W}:{CANVAS_H}[bgb];"
        f"[fg]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}[outv]"
    )
    _run(
        [
            "ffmpeg", "-y",
            "-i", str(src),
            "-t", f"{seg_len:.3f}",
            "-filter_complex", vf,
            "-map", "[outv]",
            "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p",
            str(dst),
        ],
        f"normalize {src.name} → {seg_len:.1f}s",
    )


def _seg_len(clip_dur: float, narrative: bool = False) -> float:
    """Длина сегмента из натуральной длины клипа.

    narrative=True (AI-видео фуллскрин) — без 5с-капа: 10с-клип играет целиком,
    мульти-шот не режется; но safety-кап MAX_NARRATIVE_SEG_SEC от runaway
    (битый/чужой длинный файл не превращаем в один гигантский сегмент).
    По умолчанию — прижать к [MIN, MAX] (взаимозам. B-roll).
    """
    if narrative:
        return max(MIN_SEG_SEC, min(clip_dur, MAX_NARRATIVE_SEG_SEC))
    return max(MIN_SEG_SEC, min(clip_dur, MAX_SEG_SEC))


def _build_sequence(segments: list[tuple[Path, float]], voiceover_dur: float,
                    narrative: bool = False) -> list[Path]:
    """Очередь сегментов под длину озвучки.

    По умолчанию — по кругу (idx % len) до покрытия (взаимозаменяемый B-roll).
    narrative=True (AI-видео) — ОДИН проход по порядку без повторов: клипы =
    сюжет, последний подрежется финальным -t. Без зацикливания. Если клипов
    не хватило под озвучку (недобор: часть Seedance упала / голос длиннее оценки)
    — НЕ отдаём короткий ряд (хвост = звук поверх замёрзшего/чёрного кадра),
    а падаем явно: пусть вызывающий пересоберёт. Запас клипов (+1) обычно
    покрывает; guard ловит редкий край.
    """
    sequence: list[Path] = []
    total = 0.0
    if narrative:
        for seg_path, seg_dur in segments:
            sequence.append(seg_path)
            total += seg_dur
            if total >= voiceover_dur:
                return sequence
        raise MontageError(
            f"AI-видео: клипов мало под озвучку "
            f"(видео {total:.1f}с < озвучка {voiceover_dur:.1f}с) — пересоберите")
    idx = 0
    # +1 сегмент сверху как запас — финальный -t всё равно подрежет точно.
    while total < voiceover_dur + MAX_SEG_SEC:
        seg_path, seg_dur = segments[idx % len(segments)]
        sequence.append(seg_path)
        total += seg_dur
        idx += 1
        if idx > 10000:
            raise MontageError("не удаётся набрать длину — сегменты нулевые")
    return sequence


def assemble_broll_montage(
    clip_paths: list[Path],
    voiceover_path: Path,
    output_path: Path,
    tmp_dir: Path | None = None,
    music_path: Path | None = None,
    narrative: bool = False,
) -> Path:
    """Собрать вертикальный B-roll монтаж под озвучку.

    clip_paths     — упорядоченный список путей к видеоклипам (любой aspect/
                     длина). Минимум 1. Порядок = порядок появления.
    voiceover_path — аудиофайл закадрового голоса; задаёт длину ролика.
    output_path    — куда писать финальный MP4.
    tmp_dir        — рабочая папка (по умолчанию — временная, не чистится
                     вызывающим; чистку оставляем хендлеру).
    music_path     — опц. музыкальный бэк (микшируется тихо под озвучку).
    narrative      — True для AI-видео (Seedance): клипы целиком (без 5с-капа),
                     по порядку, без зацикливания. По умолчанию — кап+круг.

    Возвращает output_path. Бросает MontageError при пустом списке клипов
    или падении ffmpeg.
    """
    clip_paths = [Path(c) for c in clip_paths]
    if not clip_paths:
        raise MontageError("пустой список клипов")
    voiceover_path = Path(voiceover_path)
    if not voiceover_path.exists():
        raise MontageError(f"озвучка не найдена: {voiceover_path}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if tmp_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="broll_montage_"))
    else:
        tmp_dir = Path(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

    voiceover_dur = _probe_duration(voiceover_path)
    if voiceover_dur <= 0:
        raise MontageError(f"озвучка нулевой длины: {voiceover_path}")
    logger.info(
        f"[broll.assembler] озвучка {voiceover_dur:.1f}s, "
        f"клипов на входе: {len(clip_paths)}"
    )

    # ── 1. Нормализовать каждый клип ПАРАЛЛЕЛЬНО ───────────────────────────
    # seg_len клипа = его натуральная длина, прижатая к [MIN, MAX].
    def _norm_one(item: tuple[int, Path]) -> tuple[int, Path, float] | None:
        i, clip = item
        if not clip.exists():
            logger.warning(f"[broll.assembler] клип пропущен (нет файла): {clip}")
            return None
        try:
            clip_dur = _probe_duration(clip)
            seg_len = _seg_len(clip_dur, narrative)
            seg_out = tmp_dir / f"seg_{i:02d}.mp4"
            _normalize_clip(clip, seg_len, seg_out)
            actual = _probe_duration(seg_out)
            return (i, seg_out, actual)
        except MontageError as e:
            logger.warning(f"[broll.assembler] клип пропущен ({clip.name}): {e}")
            return None

    with ThreadPoolExecutor(max_workers=NORMALIZE_WORKERS) as ex:
        results = list(ex.map(_norm_one, enumerate(clip_paths)))

    # Сохраняем исходный порядок клипов (ex.map порядок сохраняет, но
    # отфильтровываем None от пропущенных).
    segments: list[tuple[Path, float]] = [
        (seg, dur) for r in results if r for (_, seg, dur) in [r]
    ]
    if not segments:
        raise MontageError("ни одного клипа не удалось нормализовать")
    logger.info(f"[broll.assembler] нормализовано {len(segments)} сегментов")

    # ── 2. Выстроить очередь сегментов под длину озвучки ───────────────────
    sequence = _build_sequence(segments, voiceover_dur, narrative)
    logger.info(
        f"[broll.assembler] очередь: {len(sequence)} сегментов "
        f"({'по порядку' if narrative else 'по кругу'}, нужно {voiceover_dur:.1f}s)"
    )

    # ── 3. Concat сегментов (демукс-конкат, потоки идентичны → -c copy) ─────
    concat_list = tmp_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in sequence),
        encoding="utf-8",
    )
    montage_raw = tmp_dir / "montage_raw.mp4"
    _run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(montage_raw),
        ],
        f"concat {len(sequence)} сегментов",
    )

    # ── 4. Финал: обрезать видео точно по озвучке + замуксить аудио ────────
    if music_path and Path(music_path).exists():
        _run(
            [
                "ffmpeg", "-y",
                "-i", str(montage_raw),
                "-i", str(voiceover_path),
                "-stream_loop", "-1", "-i", str(music_path),
                "-filter_complex", (
                    # Дакинг как в music_mixer.py (селфи): музыка приглушена до -18dB
                    # И притихает под речь (sidechaincompress, ключ = голос [1:a]) —
                    # иначе голос и музыка в одном диапазоне, голос не слышно.
                    f"[2:a]volume={MUSIC_VOLUME}[mus];"
                    f"[mus][1:a]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400:makeup=1[musd];"
                    f"[1:a][musd]amix=inputs=2:duration=first:dropout_transition=0[aout]"
                ),
                "-map", "0:v:0", "-map", "[aout]",
                "-t", f"{voiceover_dur:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path),
            ],
            "финал: видео + озвучка + музыка",
        )
    else:
        _run(
            [
                "ffmpeg", "-y",
                "-i", str(montage_raw),
                "-i", str(voiceover_path),
                "-map", "0:v:0", "-map", "1:a:0",
                "-t", f"{voiceover_dur:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path),
            ],
            "финал: видео + озвучка",
        )

    out_dur = _probe_duration(output_path)
    logger.info(
        f"[broll.assembler] готово: {output_path.name}, {out_dur:.1f}s"
    )
    return output_path
