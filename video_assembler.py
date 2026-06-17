"""Auto-montage module: builds a 9:16 video from avatar + B-roll.

Two layout modes:

1. ``split`` (default, original) — 50/50 vertical stack:
   ┌────────────┐
   │ B-roll top │  1080×960
   ├────────────┤
   │ Avatar bot │  1080×960
   └────────────┘

2. ``dynamic`` — full-screen alternating:
   Avatar (full 1080×1920) plays continuously with its audio.
   B-roll clips overlay it at evenly-spaced intervals, each for up to
   MAX_BROLL_SEC seconds.  Gives a more cinematic look.

After assembly, subtitles can be burned via ``subtitle_burner``.

Usage:
    from video_assembler import assemble_auto_montage
    out = assemble_auto_montage(project_dir, layout="dynamic", subtitles=True)
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("video_assembler")

CANVAS_W = 1080
CANVAS_H = 1920
HALF_H = CANVAS_H // 2  # 960
FPS = 30
MAX_BROLL_SEC = 5  # max duration of each B-roll insert in dynamic mode

# Avatar overscan — HeyGen sometimes returns the avatar frame slightly
# narrower than 1080 (a 1-3 px bright border along left/right edges),
# which becomes visible when the studio background is bright. We scale
# the avatar up by this factor before center-cropping so any edge
# artifact falls outside the final crop — the CapCut "101-103%" trick.
# 1.025 = 2.5% overscan, eats ~13px from each side, keeps head centered.
AVATAR_OVERSCAN = 1.025

# ── Floating avatar (PiP) ─────────────────────────────────────────────────
# Круглый talking-head поверх fullscreen B-roll (формат «плавающий аватар»,
# запрос Артёма 7 июня 2026). Кружок снизу-справа по умолчанию.
PIP_DIAMETER = 340          # диаметр круглого PiP, px (на canvas 1080×1920)
PIP_MARGIN = 56             # отступ от краёв кадра, px
PIP_CORNER = "br"           # br=снизу-справа (выбор Артёма), bl/tr/tl


def _pip_overlay_position(
    canvas_w: int, canvas_h: int, diameter: int, margin: int, corner: str = "br"
) -> tuple[int, int]:
    """Координаты (x, y) левого-верхнего угла квадрата PiP для overlay в углу.

    corner: 'br' снизу-справа (дефолт), 'bl' снизу-слева, 'tr' сверху-справа,
    'tl' сверху-слева. Кружок гарантированно в пределах кадра.
    """
    right_x = canvas_w - diameter - margin
    bottom_y = canvas_h - diameter - margin
    return {
        "br": (right_x, bottom_y),
        "bl": (margin, bottom_y),
        "tr": (right_x, margin),
        "tl": (margin, margin),
    }.get(corner, (right_x, bottom_y))


# Font directory on the server (Montserrat Black etc.)
FONT_DIR = Path(__file__).parent / "assets" / "fonts"


class AssemblyError(Exception):
    """Raised when auto-montage fails."""


def _run(cmd: list[str], desc: str, timeout: int = 300) -> str:
    """Run a subprocess; raise AssemblyError on failure. Returns stdout."""
    logger.info(f"[assembler] {desc}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or "")[-800:]
        logger.error(f"[assembler] {desc} failed:\n{tail}")
        raise AssemblyError(f"{desc} failed: {tail}")
    return result.stdout


def _probe_duration(video_path: Path) -> float:
    """Return video duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssemblyError(f"ffprobe failed for {video_path}: {result.stderr[-400:]}")
    return float(json.loads(result.stdout)["format"]["duration"])


def _probe_aspect(video_path: Path) -> float:
    """Return width/height ratio of the first video stream (None-safe).

    Returns 1.0 on failure so callers can treat unprobeable clips as square
    (they'll land in the letterbox path, which is the safe default).
    """
    try:
        res = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        w_str, h_str = res.stdout.strip().split(",")[:2]
        w, h = int(w_str), int(h_str)
        if w <= 0 or h <= 0:
            return 1.0
        return w / h
    except Exception as e:
        logger.warning(f"[assembler] _probe_aspect({video_path.name}) failed: {e}")
        return 1.0


def _find_avatar(project_dir: Path) -> Path:
    """Pick the most recent avatar_*.mp4 in the project folder."""
    candidates = sorted(
        project_dir.glob("avatar_*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise AssemblyError(
            "Не найден файл аватара (avatar_*.mp4) в папке проекта. "
            "Сгенерируй аватар через HeyGen перед авто-сборкой."
        )
    return candidates[0]


def _find_broll(project_dir: Path) -> list[Path]:
    """Return broll_*.mp4 files sorted by numeric suffix."""
    def _sort_key(p: Path) -> int:
        try:
            return int(p.stem.split("_")[-1])
        except (ValueError, IndexError):
            return 999
    return sorted(project_dir.glob("broll_*.mp4"), key=_sort_key)


# ═══════════════════════════════════════════════════════════════════════════════
#  Photo → Ken Burns clip (fallback B-roll source)
# ═══════════════════════════════════════════════════════════════════════════════

PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _find_photo_library() -> Path | None:
    """Locate broll-library/photos directory (project-relative).

    Structure expected::

        broll-library/photos/<subfolder>/*.jpg
        broll-library/photos/<subfolder>/*.png

    Subfolders are optional — loose files in ``photos/`` are also picked up.
    Returns None if the directory doesn't exist.
    """
    root = Path(__file__).parent / "broll-library" / "photos"
    return root if root.exists() else None


def _list_library_photos(library_dir: Path) -> list[Path]:
    """Recursively list all image files under the photo library."""
    photos: list[Path] = []
    for ext in PHOTO_EXTS:
        photos.extend(library_dir.rglob(f"*{ext}"))
        photos.extend(library_dir.rglob(f"*{ext.upper()}"))
    return sorted(set(photos))


def _find_project_photos(project_dir: Path) -> list[Path]:
    """Return per-project photos sorted by name.

    Looks in two places (in order):
      1. ``project_dir/photos/`` — preferred location for client photos
         uploaded to a specific project (e.g. shoe-brand product shots).
      2. ``project_dir/photo_*.{jpg,png,webp}`` — loose files in project root,
         convenient for SCP'd images.

    Used by :func:`assemble_auto_montage` to MIX project photos with project
    videos in one montage (not as a fallback — always added if present).
    """
    photos: list[Path] = []
    photo_dir = project_dir / "photos"
    if photo_dir.exists():
        for ext in PHOTO_EXTS:
            photos.extend(photo_dir.glob(f"*{ext}"))
            photos.extend(photo_dir.glob(f"*{ext.upper()}"))
    # Loose files in project root (photo_*.jpg, photo_*.png, ...)
    for ext in PHOTO_EXTS:
        photos.extend(project_dir.glob(f"photo_*{ext}"))
        photos.extend(project_dir.glob(f"photo_*{ext.upper()}"))
    return sorted(set(photos))


def _resolve_photo_anchor(photo_path: Path, brand_default: float) -> float:
    """Resolve the split-crop anchor for a single photo.

    The anchor is a float 0.0 (crop shows the TOP of the Ken-Burns clip) …
    1.0 (crop shows the BOTTOM). Resolution order (first match wins):

      1. Filename suffix before extension:
         ``*_top``    → 0.0
         ``*_upper``  → 0.25
         ``*_center`` → 0.5
         ``*_lower``  → 0.75
         ``*_bottom`` → 1.0
      2. Sidecar file ``<photo>.anchor.txt`` containing a single float 0-1.
      3. ``brand_default`` (e.g. 1.0 for the shoe brand — shoes sit at the
         bottom of full-body photos by convention, 0.5 for default brand).

    This lets users override the auto-crop per photo without editing code —
    e.g. for a photo where the shoe is held up to the camera ("подошва сверху")
    rename it to ``mypic_center.jpg`` and the slot will crop its middle.
    """
    stem = photo_path.stem.lower()
    suffix_map = {
        "_top": 0.0,
        "_upper": 0.25,
        "_center": 0.5,
        "_middle": 0.5,
        "_lower": 0.75,
        "_bottom": 1.0,
    }
    for suf, val in suffix_map.items():
        if stem.endswith(suf):
            return val

    # Sidecar file (one-off tuning without renaming)
    for name in (f"{photo_path.name}.anchor.txt", f"{photo_path.stem}.anchor.txt"):
        side = photo_path.parent / name
        if side.exists():
            try:
                val = float(side.read_text(encoding="utf-8").strip())
                return max(0.0, min(1.0, val))
            except Exception as e:
                logger.warning(f"[assembler] bad anchor file {side}: {e}")

    return max(0.0, min(1.0, brand_default))


# Дефолтный split-anchor для бренда shoes. Геометрически выверен (см.
# _split_visible_photo_band + TDD test_split_anchor_keeps_shoe_in_frame):
# обувь на типичном lifestyle-фото лежит в нижних [0.60, 0.95] высоты;
# чтобы она попадала в split-слот ЦЕЛИКОМ на всех кадрах Ken Burns
# (zoom 1.0→1.06), anchor должен быть высоким (низ фото). 0.75/0.62 срезали
# обувь снизу. 1.0 = нижняя половина фото — обувь целиком + минимум пола.
SHOES_SPLIT_ANCHOR = 1.0


def _split_visible_photo_band(anchor: float, zoom: float = 1.0) -> tuple[float, float]:
    """Какая вертикальная зона исходного фото (доли 0..1) реально видна в
    split-слоте 1080×960 после Ken Burns (центр-зум, фактор ``zoom``) +
    split-crop с данным ``anchor``.

    Конвейер:
      • Ken Burns output 1920px = зона фото [0.5-0.5/zoom, 0.5+0.5/zoom]
        (центрированный зум, y='ih/2-(ih/zoom/2)').
      • Split-crop берёт полосу 960px из 1920 с верхним краем на anchor*960,
        т.е. в долях output: [anchor*0.5, anchor*0.5+0.5].

    Возвращает (top_frac, bot_frac) — границы видимой зоны в координатах фото.
    """
    zoom = max(zoom, 1.0)
    zone_top = 0.5 - 0.5 / zoom
    zone_h = 1.0 / zoom
    band_top = zone_top + (anchor * 0.5) * zone_h
    band_bot = zone_top + (anchor * 0.5 + 0.5) * zone_h
    return (band_top, band_bot)


def _shoe_anchor_keeps_shoe_visible(
    shoe_top: float, shoe_bot: float, anchor: float, zooms=(1.0, 1.06)
) -> bool:
    """True если зона обуви [shoe_top, shoe_bot] (доли высоты фото) полностью
    попадает в split-слот на ВСЕХ кадрах Ken Burns (zoom от 1.0 до 1.06).
    Используется и в проде (валидация), и в TDD."""
    for z in zooms:
        bt, bb = _split_visible_photo_band(anchor, z)
        if not (bt <= shoe_top + 1e-9 and bb >= shoe_bot - 1e-9):
            return False
    return True


def _build_ken_burns_clips(
    photos: list[Path],
    tmp_dir: Path,
    clip_duration: float,
    name_prefix: str = "photo_clip",
    variants: list[str] | None = None,
) -> list[Path]:
    """Convert a list of photos to Ken Burns mp4 clips.

    Returns the list of generated clips (skips any that fail). If ``variants``
    is None, rotates through the default set so consecutive clips have
    different motion direction. Pass a single-element list (e.g.
    ``["zoom_in_shoes"]``) to force every photo to use one variant — useful
    for product brands where the subject sits at a consistent position in
    every shot.
    """
    if variants is None:
        variants = ["zoom_in", "zoom_in_left", "zoom_in_right", "zoom_in_up"]
    if not variants:
        variants = ["zoom_in"]
    clips: list[Path] = []
    for i, img in enumerate(photos):
        variant = variants[i % len(variants)]
        clip_out = tmp_dir / f"{name_prefix}_{i:02d}.mp4"
        try:
            _image_to_broll_clip(img, clip_out, clip_duration, variant)
            clips.append(clip_out)
        except Exception as e:
            logger.warning(f"[assembler] {name_prefix} from {img.name} failed: {e}")
    return clips


def _plan_smart_mixed_montage(
    video_paths: list[Path],
    photo_clips: list[Path],
    photo_clip_dur: float,
    avatar_duration: float,
    intro_dur: float = 1.5,
    outro_dur: float = 2.0,
) -> list[dict]:
    """Build a deterministic montage plan for mixed video + photo B-roll.

    Rules (the "smart" contract):
      - INTRO: ``avatar_full`` for ``intro_dur`` seconds — hook with the face.
      - BODY: alternating video (``broll_full``) and photo (``split``) segments.
        Pattern per round: 1 video → up to 2 photos → 1 video → 2 photos, …
      - Each video segment lasts EXACTLY the full video-clip duration — never
        cut mid-clip. Each photo segment lasts exactly ``photo_clip_dur``
        (Ken Burns was built at that length).
      - OUTRO: ``avatar_full`` for ``outro_dur`` seconds — CTA.
      - If body total > active window: drop clips from the END (preserves
        openings, keeps CTA, never cuts mid-clip).
      - If body total < active window: stretch outro (CTA gets more breathing
        room; avoids awkward silent gaps mid-body).
      - ``broll_index`` in output refers to the concatenated
        ``video_paths + photo_clips`` list — assembler consumes the two as a
        single broll_paths list.

    Returns a list of segment dicts compatible with :func:`_assemble_pro`.
    """
    if not video_paths and not photo_clips:
        raise AssemblyError(
            "Smart montage requires at least one video or one photo in the project."
        )

    # Real video durations — we NEVER crop a video mid-play.
    video_durations = [_probe_duration(p) for p in video_paths]

    n_videos = len(video_paths)
    # Indices into the combined broll_paths list that the assembler will see
    v_indices = list(range(n_videos))
    p_indices = list(range(n_videos, n_videos + len(photo_clips)))

    # Body: (broll_index, layout, duration). Greedy alternation.
    body: list[tuple[int, str, float]] = []
    while v_indices or p_indices:
        if v_indices:
            vi = v_indices.pop(0)
            body.append((vi, "broll_full", video_durations[vi]))
        # Up to 2 photos after each video for variety; if no videos left,
        # photos run sequentially.
        for _ in range(2):
            if p_indices:
                pi = p_indices.pop(0)
                body.append((pi, "split", photo_clip_dur))
            else:
                break

    active_window = max(0.0, avatar_duration - intro_dur - outro_dur)

    # Fit: drop clips from the end until body fits inside active_window.
    # Never slice a clip — always drop it whole.
    def _body_total(b: list[tuple[int, str, float]]) -> float:
        return sum(d for _, _, d in b)

    while body and _body_total(body) > active_window:
        body.pop()

    total_body = _body_total(body)
    slack = active_window - total_body  # ≥ 0 after the loop above
    final_outro = outro_dur + slack     # absorb slack into CTA

    # Build the actual plan with timestamps.
    plan: list[dict] = [
        {"start": 0.0, "end": intro_dur, "layout": "avatar_full", "broll_index": None}
    ]
    t = intro_dur
    for bi, layout, dur in body:
        plan.append({"start": t, "end": t + dur, "layout": layout, "broll_index": bi})
        t += dur
    plan.append({"start": t, "end": t + final_outro, "layout": "avatar_full", "broll_index": None})

    # Snap final end to avatar_duration to avoid float drift (a few ms).
    if abs(plan[-1]["end"] - avatar_duration) > 0.001:
        plan[-1]["end"] = avatar_duration

    logger.info(
        f"[assembler] smart plan: {len(plan)} segments "
        f"({n_videos} video-full, {len(photo_clips)} photo-split, "
        f"intro={intro_dur}s, outro={final_outro:.1f}s, dropped="
        f"{(n_videos + len(photo_clips)) - len([s for s in plan if s['broll_index'] is not None])})"
    )
    return plan


def _plan_fullscreen_only_montage(
    video_paths: list[Path],
    photo_clips: list[Path],
    photo_clip_dur: float,
    avatar_duration: float,
    intro_dur: float = 2.0,
    outro_dur: float = 3.0,
) -> list[dict]:
    """Build a fullscreen-only montage plan: avatar shows ONLY at start
    (intro hook) and end (outro CTA). All B-roll plays sequentially
    on full screen — no split, no avatar in the middle.

    Use case (5 мая 2026, по reportу Артёма): shoes lifestyle-фото 9:16
    где модель и обувь видны полностью. Split-секция (1080×960 верхняя)
    обрезает фото и теряет важное (лоферы в руках, на ногах, контекст).

    Rules:
      - INTRO: ``avatar_full`` for ``intro_dur`` seconds.
      - BODY: ALL B-roll sequential as ``broll_full`` (videos play full
        clip duration, photos play ``photo_clip_dur`` seconds each).
        NO chrono of avatar in the middle.
      - OUTRO: ``avatar_full`` for ``outro_dur`` seconds (CTA).
      - If body total > active window: drop clips from END.
      - If body total < active window: stretch outro.
      - Order: videos first (real footage), then Ken Burns photos.

    Returns segment list compatible with :func:`_assemble_pro`.
    """
    if not video_paths and not photo_clips:
        raise AssemblyError(
            "Fullscreen-only montage requires at least one video or one photo."
        )

    video_durations = [_probe_duration(p) for p in video_paths]
    n_videos = len(video_paths)

    # Body: videos first, then photos. All as broll_full.
    body: list[tuple[int, str, float]] = []
    for vi in range(n_videos):
        body.append((vi, "broll_full", video_durations[vi]))
    for pi in range(len(photo_clips)):
        body.append((n_videos + pi, "broll_full", photo_clip_dur))

    active_window = max(0.0, avatar_duration - intro_dur - outro_dur)

    def _body_total(b: list[tuple[int, str, float]]) -> float:
        return sum(d for _, _, d in b)

    while body and _body_total(body) > active_window:
        body.pop()

    total_body = _body_total(body)
    slack = active_window - total_body
    final_outro = outro_dur + slack

    plan: list[dict] = [
        {"start": 0.0, "end": intro_dur, "layout": "avatar_full", "broll_index": None}
    ]
    t = intro_dur
    for bi, layout, dur in body:
        plan.append({"start": t, "end": t + dur, "layout": layout, "broll_index": bi})
        t += dur
    plan.append({"start": t, "end": t + final_outro, "layout": "avatar_full", "broll_index": None})

    if abs(plan[-1]["end"] - avatar_duration) > 0.001:
        plan[-1]["end"] = avatar_duration

    logger.info(
        f"[assembler] fullscreen-only plan: {len(plan)} segments "
        f"({n_videos} video-full, {len(photo_clips)} photo-full, "
        f"intro={intro_dur}s, outro={final_outro:.1f}s, dropped="
        f"{(n_videos + len(photo_clips)) - len([s for s in plan if s['broll_index'] is not None])})"
    )
    return plan


def _plan_floating_montage(
    video_paths: list[Path],
    photo_clips: list[Path],
    photo_clip_dur: float,
    avatar_duration: float,
    intro_dur: float = 5.0,
    outro_dur: float = 4.0,
) -> list[dict]:
    """План «плавающего аватара» (floating PiP).

    Структура (запрос Артёма 7 июня 2026, референс x5.mov):
      - INTRO: ``avatar_full`` на ``intro_dur`` секунд (hook на весь экран).
      - BODY: ВСЕ B-roll последовательно как ``pip`` — B-roll на весь экран,
        talking-head аватар маленьким КРУГЛЫМ PiP в углу (снизу-справа).
        Видео играют свою длину, фото — ``photo_clip_dur`` каждое.
      - OUTRO: ``avatar_full`` на ``outro_dur`` секунд (CTA на весь экран).
      - Если body > активного окна — дропаем клипы с конца.
      - Если body < окна — растягиваем outro.

    Отличие от fullscreen-плана только в layout body-сегментов: ``pip``
    вместо ``broll_full`` (аватар не исчезает, висит кружком). Возвращает
    список сегментов, совместимый с :func:`_assemble_pro`.
    """
    if not video_paths and not photo_clips:
        raise AssemblyError(
            "Floating montage requires at least one video or one photo."
        )

    video_durations = [_probe_duration(p) for p in video_paths]
    n_videos = len(video_paths)

    body: list[tuple[int, str, float]] = []
    for vi in range(n_videos):
        body.append((vi, "pip", video_durations[vi]))
    for pi in range(len(photo_clips)):
        body.append((n_videos + pi, "pip", photo_clip_dur))

    active_window = max(0.0, avatar_duration - intro_dur - outro_dur)

    def _body_total(b: list[tuple[int, str, float]]) -> float:
        return sum(d for _, _, d in b)

    while body and _body_total(body) > active_window:
        body.pop()

    total_body = _body_total(body)
    slack = active_window - total_body
    final_outro = outro_dur + slack

    plan: list[dict] = [
        {"start": 0.0, "end": intro_dur, "layout": "avatar_full", "broll_index": None}
    ]
    t = intro_dur
    for bi, layout, dur in body:
        plan.append({"start": t, "end": t + dur, "layout": layout, "broll_index": bi})
        t += dur
    plan.append({"start": t, "end": t + final_outro, "layout": "avatar_full", "broll_index": None})

    if abs(plan[-1]["end"] - avatar_duration) > 0.001:
        plan[-1]["end"] = avatar_duration

    logger.info(
        f"[assembler] floating plan: {len(plan)} segments "
        f"({n_videos} video-pip, {len(photo_clips)} photo-pip, "
        f"intro={intro_dur}s, outro={final_outro:.1f}s, dropped="
        f"{(n_videos + len(photo_clips)) - len([s for s in plan if s['broll_index'] is not None])})"
    )
    return plan


def _ken_burns_filter(duration_frames: int, variant: str) -> str:
    """Build an ffmpeg filter chain for a Ken Burns clip at 1080x1920.

    The source image is first upscaled to a high-resolution 9:16 canvas
    (4320x7680) so that ``zoompan`` has enough pixels to work with without
    visible aliasing or micro-jitter. Then ``zoompan`` produces the 1080x1920
    output at 30 fps.

    Variants
    --------
    - ``zoom_in``       — static center, gradually zooms in.
    - ``zoom_in_left``  — zooms in while panning toward the left edge.
    - ``zoom_in_right`` — zooms in while panning toward the right edge.
    - ``zoom_in_up``    — zooms in with a slow vertical drift upward.
    - ``zoom_in_shoes`` — tight zoom (1.8→2.0) on the bottom half of the
      frame. Designed for full-body product shots where the subject
      (shoes, handbag, etc.) sits at the bottom edge of the photo and
      would otherwise get cropped out when the clip lands in a split
      slot. See ``_shoes_ken_burns`` math note below.
    """
    # Final zoom level (1.0 → no zoom, 1.15 → 15% tighter)
    z_end = 1.15
    # Per-frame zoom step
    z_step = (z_end - 1.0) / max(duration_frames - 1, 1)

    base = (
        f"scale=4320:7680:force_original_aspect_ratio=increase,"
        f"crop=4320:7680,setsar=1,"
    )

    # ── zoom_in_shoes: gentle, centre-anchored ──
    # Earlier version zoomed 1.8→2.0 on a bottom-anchored window so the
    # split slot (which cropped the centre of the Ken-Burns clip) would land
    # on the shoes. In practice that cropped the photo horizontally (zoom
    # applies to BOTH axes) and cut shoes by the sides; at the same time the
    # vertical anchor was off because the shoes live at ~85–95% of the
    # original height and the centre-crop only showed 62–88%.
    #
    # Fixed approach: Ken Burns keeps a gentle centred zoom (like the default
    # variant) and the SPLIT CROP in _assemble_pro anchors to the BOTTOM of
    # the 1080×1920 clip for shoe-brand photo indices — so the y=960..1920
    # band, where the shoes actually are, ends up in the 1080×960 slot.
    if variant == "zoom_in_shoes":
        # 7 июня 2026 (фидбэк Артёма): старый центр-зум 1.0→1.15 был «тупым
        # увеличением» (без движения) И слишком сильным — обувь резалась.
        # Новое:
        #   • Мягкий зум 1.0→1.06 — вдвое меньше наезда, обувь целиком.
        #   • Лёгкий ГОРИЗОНТАЛЬНЫЙ дрейф окна (едет 40%→60% по ширине) + зум =
        #     диагональное «живое» движение, как просил Артём.
        #   • По вертикали окно центрировано: вертикальную зону обуви выбирает
        #     СПЛИТ-КРОП (anchor 0.75) ниже по пайплайну — поэтому Y здесь НЕ
        #     трогаем, иначе двойной bottom-anchor конфликтует.
        z_end_shoes = 1.06
        z_step_shoes = (z_end_shoes - 1.0) / max(duration_frames - 1, 1)
        df = max(duration_frames - 1, 1)
        x_expr = f"(iw-iw/zoom)*(0.40+0.20*on/{df})"
        y_expr = "ih/2-(ih/zoom/2)"
        zoompan = (
            f"zoompan="
            f"z='min(zoom+{z_step_shoes:.6f},{z_end_shoes})':"
            f"d={duration_frames}:"
            f"x='{x_expr}':y='{y_expr}':"
            f"s={CANVAS_W}x{CANVAS_H}:fps={FPS}"
        )
        return base + zoompan

    if variant == "zoom_in_shoes_full":
        # Fullscreen-only Ken Burns с **bottom-anchor**.
        # Создан 5 мая 2026 после feedback Артёма: на full-screen эталонном
        # ролике #2 («Лоферы рождаются под тебя») центр-зум при 1.15× уезжал
        # обувь вниз из кадра. Для shoes-фото где обувь в самой нижней части
        # original (lifestyle с ногами модели) — нужно якорить нижний край.
        #
        # Математика: y='ih-ih/zoom' значит верх window = (ih − height_window),
        # т.е. window идёт от (ih−ih/zoom) до ih. Bottom фиксирован на ih (низ
        # original фото), top плавно двигается вверх по мере zoom. Обувь
        # которая в нижних 5-10% original остаётся в кадре весь клип.
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih-ih/zoom"  # bottom-edge anchor
        zoompan = (
            f"zoompan="
            f"z='min(zoom+{z_step:.6f},{z_end})':"
            f"d={duration_frames}:"
            f"x='{x_expr}':y='{y_expr}':"
            f"s={CANVAS_W}x{CANVAS_H}:fps={FPS}"
        )
        return base + zoompan

    if variant == "zoom_in_left":
        x_expr = "0"
        y_expr = "ih/2-(ih/zoom/2)"
    elif variant == "zoom_in_right":
        x_expr = "iw-(iw/zoom)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif variant == "zoom_in_up":
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "0"
    else:  # zoom_in (center)
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    zoompan = (
        f"zoompan="
        f"z='min(zoom+{z_step:.6f},{z_end})':"
        f"d={duration_frames}:"
        f"x='{x_expr}':y='{y_expr}':"
        f"s={CANVAS_W}x{CANVAS_H}:fps={FPS}"
    )
    return base + zoompan


def _image_to_broll_clip(
    image_path: Path,
    output_path: Path,
    duration: float,
    variant: str,
) -> Path:
    """Convert a still image to a ``CANVAS_W × CANVAS_H`` silent video clip.

    ``duration`` is in seconds. ``variant`` picks a Ken Burns motion style;
    see :func:`_ken_burns_filter`.
    """
    frames = max(int(round(duration * FPS)), FPS)  # at least 1 second
    vf = _ken_burns_filter(frames, variant)

    _run(
        [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(image_path),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-r", str(FPS),
            "-an",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "15",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ],
        f"ken-burns {variant} from {image_path.name}",
        timeout=120,
    )
    return output_path


def _gather_photo_broll(
    project_dir: Path,
    count: int,
    clip_duration: float,
    tmp_dir: Path,
) -> list[Path]:
    """Build a list of photo-based B-roll clips as a fallback source.

    Picks ``count`` random images from the global photo library, generates
    Ken Burns clips for each into ``tmp_dir``, and returns the paths. Returns
    an empty list if no library exists or no images are found.

    Used by :func:`assemble_auto_montage` ONLY when the project has neither
    its own videos nor its own photos. For project-specific photo MIX, see
    :func:`_find_project_photos` + :func:`_build_ken_burns_clips`.
    """
    import random

    library = _find_photo_library()
    if not library:
        logger.info("[assembler] photo library not found — skipping photo fallback")
        return []

    photos = _list_library_photos(library)
    if not photos:
        logger.info(f"[assembler] photo library {library} is empty — skipping")
        return []

    # Pick random subset (avoid repetition if pool is big enough)
    if len(photos) >= count:
        picks = random.sample(photos, count)
    else:
        picks = random.choices(photos, k=count)

    clips = _build_ken_burns_clips(picks, tmp_dir, clip_duration, name_prefix="photo_broll")
    logger.info(
        f"[assembler] generated {len(clips)} library-photo clips "
        f"({count} requested, {len(photos)} in library, duration {clip_duration:.1f}s each)"
    )
    return clips


# ═══════════════════════════════════════════════════════════════════════════════
#  Layout: SPLIT (original 50/50 stack)
# ═══════════════════════════════════════════════════════════════════════════════

def _assemble_split(
    avatar_path: Path,
    broll_paths: list[Path],
    avatar_duration: float,
    tmp_dir: Path,
    output_path: Path,
) -> Path:
    """Build split-screen: B-roll top + avatar bottom.

    Two sub-layouts, chosen automatically from B-roll aspect:

    * **compact** (landscape-dominant sources, max aspect > 1.3):
      B-roll takes its natural fit-width height (e.g. 608px for 16:9),
      avatar fills the remaining height. Eliminates black bars around
      horizontal demos while preserving the full frame — nothing is cropped
      off the sides of the B-roll. Avatar gets more vertical real estate
      (up to 1312px vs the classic 960px), which means head and shoulders
      at a more natural size.

    * **classic 50/50** (portrait or square-dominant sources, max aspect ≤ 1.3):
      B-roll letterboxed into 1080×960, avatar in 1080×960. Side black bars
      for portrait sources are smaller than letterbox bars would be, and the
      symmetric 50/50 rhythm reads better when sources are themselves tall.

    In both modes the fit policy for B-roll is *decrease + pad* (letterbox),
    never crop-to-fill: demo-video edges (UI, text, graphs) must stay visible.
    The dimension rebalancing in compact mode is what removes the bars, not
    a switch to cropping.
    """
    n = len(broll_paths)
    segment_duration = avatar_duration / n

    # ── 0. Probe aspects → decide compact vs classic ───────────────────────
    aspects = [_probe_aspect(p) for p in broll_paths]
    max_aspect = max(aspects) if aspects else 1.0
    # Compact mode kicks in for landscape-dominant sources. 1.3 sits just
    # below 4:3 (1.33), so 4:3 demos also get the extra avatar real estate.
    compact_mode = max_aspect > 1.3

    if compact_mode:
        # Size the B-roll slot to the widest clip's natural fit-width height.
        # Narrower-but-still-landscape siblings (e.g. 4:3 next to 16:9) will
        # side-pad inside the slot, which is far less ugly than top+bottom
        # bars on every clip.
        broll_h = int(CANVAS_W / max_aspect)
        broll_h = broll_h - (broll_h % 2)           # even (H.264 constraint)
        broll_h = max(480, min(broll_h, HALF_H))    # clamp to [480, 960]
        avatar_h = CANVAS_H - broll_h               # e.g. 1312 for 16:9
        avatar_h = avatar_h - (avatar_h % 2)
        logger.info(
            f"[assembler] split compact: max_aspect={max_aspect:.2f} "
            f"→ broll_h={broll_h}, avatar_h={avatar_h}"
        )
    else:
        broll_h = HALF_H
        avatar_h = HALF_H
        logger.info(
            f"[assembler] split classic 50/50: max_aspect={max_aspect:.2f} ≤ 1.3"
        )

    # ── 1. Normalize each B-roll → 1080×broll_h, loop if short ─────────────
    broll_segments: list[Path] = []
    for i, clip in enumerate(broll_paths):
        seg_out = tmp_dir / f"broll_seg_{i:02d}.mp4"
        _run(
            [
                "ffmpeg", "-y",
                "-stream_loop", "-1",
                "-i", str(clip),
                "-t", f"{segment_duration:.3f}",
                "-vf", (
                    f"scale={CANVAS_W}:{broll_h}:force_original_aspect_ratio=decrease,"
                    f"pad={CANVAS_W}:{broll_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                    f"setsar=1,fps={FPS}"
                ),
                "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-pix_fmt", "yuv420p",
                str(seg_out),
            ],
            f"normalize broll {i+1}/{n}",
        )
        broll_segments.append(seg_out)

    # ── 2. Concat B-roll segments ──────────────────────────────────────────
    concat_list = tmp_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{seg.name}'" for seg in broll_segments),
        encoding="utf-8",
    )
    broll_top = tmp_dir / "broll_top.mp4"
    _run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_list), "-c", "copy", str(broll_top)],
        "concat broll segments",
    )

    # ── 3. Normalize avatar → 1080×avatar_h with overscan ──────────────────
    # Overscan (~2.5%) eats HeyGen's 1-3px edge borders before cropping.
    # AVATAR_CROP_Y=280 keeps the framing consistent across both modes: in
    # compact mode the crop window just extends further down the torso,
    # which reads naturally because the avatar is standing/seated upright.
    AVATAR_CROP_Y = 280
    overscan_w = int(CANVAS_W * AVATAR_OVERSCAN)    # 1107
    overscan_h = int(avatar_h * AVATAR_OVERSCAN)
    avatar_bot = tmp_dir / "avatar_bot.mp4"
    _run(
        [
            "ffmpeg", "-y", "-i", str(avatar_path),
            "-vf", (
                f"scale={overscan_w}:{overscan_h}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{avatar_h}:(iw-{CANVAS_W})/2:"
                f"min({AVATAR_CROP_Y}\\,ih-{avatar_h}),setsar=1,fps={FPS}"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(avatar_bot),
        ],
        "normalize avatar",
    )

    # ── 4. Vstack broll (top) + avatar (bottom) ────────────────────────────
    _run(
        [
            "ffmpeg", "-y",
            "-i", str(broll_top), "-i", str(avatar_bot),
            "-filter_complex", "[0:v][1:v]vstack=inputs=2[outv]",
            "-map", "[outv]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest",
            str(output_path),
        ],
        "vstack final",
    )
    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
#  Layout: DYNAMIC (full-screen alternating avatar ↔ B-roll)
# ═══════════════════════════════════════════════════════════════════════════════

def _assemble_dynamic(
    avatar_path: Path,
    broll_paths: list[Path],
    avatar_duration: float,
    tmp_dir: Path,
    output_path: Path,
) -> Path:
    """Full-screen alternating: avatar plays underneath, B-roll overlays at intervals.

    Avatar video+audio run continuously.  B-roll clips are overlaid
    at evenly-spaced timestamps so the audio (lip-sync) stays in sync.

    Quality rules:
    - Each B-roll clip plays at least MIN_CLIP_SEC (3s)
    - Avatar gap between clips at least MIN_GAP_SEC (2.5s)
    - If too many clips, pick evenly spaced subset
    - B-roll occupies ~50-60% of video, avatar ~40-50%
    """
    MIN_CLIP_SEC = 3.0    # minimum B-roll clip duration
    MIN_GAP_SEC = 2.5     # minimum avatar-only gap between clips
    BROLL_RATIO = 0.55    # target: B-roll takes 55% of video

    if not broll_paths:
        raise AssemblyError("Нет B-roll клипов для динамического монтажа.")

    # Probe real durations
    raw_durations = []
    for clip in broll_paths:
        dur = min(_probe_duration(clip), MAX_BROLL_SEC)
        raw_durations.append(max(dur, MIN_CLIP_SEC))

    # Calculate max clips that fit with quality constraints
    # Each clip needs MIN_CLIP_SEC + MIN_GAP_SEC of timeline space
    # Plus one extra gap at the start
    max_clips = max(1, int((avatar_duration - MIN_GAP_SEC) / (MIN_CLIP_SEC + MIN_GAP_SEC)))

    # Pick evenly spaced clips if we have too many
    n_available = len(broll_paths)
    if n_available > max_clips:
        # Pick evenly spaced indices
        step = n_available / max_clips
        selected_indices = [int(i * step) for i in range(max_clips)]
        broll_paths = [broll_paths[i] for i in selected_indices]
        raw_durations = [raw_durations[i] for i in selected_indices]
        logger.info(
            f"[assembler] dynamic: too many clips ({n_available}), "
            f"selected {max_clips} evenly spaced"
        )

    n = len(broll_paths)

    # Calculate per-clip duration to hit target ratio
    target_broll_total = avatar_duration * BROLL_RATIO
    clip_duration = max(MIN_CLIP_SEC, target_broll_total / n)

    # Ensure avatar gaps are not too short
    total_broll_time = clip_duration * n
    avatar_only_time = avatar_duration - total_broll_time
    gap = avatar_only_time / (n + 1)

    # If gaps too short, reduce clip duration
    if gap < MIN_GAP_SEC:
        # Solve: n * clip_dur + (n+1) * MIN_GAP_SEC = avatar_duration
        clip_duration = max(MIN_CLIP_SEC, (avatar_duration - (n + 1) * MIN_GAP_SEC) / n)
        total_broll_time = clip_duration * n
        avatar_only_time = avatar_duration - total_broll_time
        gap = avatar_only_time / (n + 1)

    # If still too tight (shouldn't happen with max_clips calc), reduce clips
    while gap < MIN_GAP_SEC * 0.8 and n > 1:
        n -= 1
        broll_paths = broll_paths[:n]
        clip_duration = max(MIN_CLIP_SEC, (avatar_duration - (n + 1) * MIN_GAP_SEC) / n)
        total_broll_time = clip_duration * n
        avatar_only_time = avatar_duration - total_broll_time
        gap = avatar_only_time / (n + 1)

    broll_durations = [clip_duration] * n

    # Build timeline: [(start_sec, duration, clip_index), ...]
    timeline = []
    cursor = gap
    for i, dur in enumerate(broll_durations):
        timeline.append((cursor, dur, i))
        cursor += dur + gap

    logger.info(
        f"[assembler] dynamic layout: {n}/{n_available} broll clips, "
        f"clip={clip_duration:.1f}s, gap={gap:.1f}s, total={avatar_duration:.1f}s"
    )

    # ── Scale avatar to full 1080×1920 with overscan (HeyGen edge-border fix) ──
    avatar_full = tmp_dir / "avatar_full.mp4"
    _full_w = int(CANVAS_W * AVATAR_OVERSCAN)   # 1107
    _full_h = int(CANVAS_H * AVATAR_OVERSCAN)   # 1968
    _run(
        [
            "ffmpeg", "-y", "-i", str(avatar_path),
            "-vf", (
                f"scale={_full_w}:{_full_h}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},setsar=1,fps={FPS}"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(avatar_full),
        ],
        "scale avatar full-screen",
    )

    # ── Scale + loop each B-roll to 1080×1920 ──
    broll_inputs: list[str] = []

    for idx, (start_sec, dur, clip_i) in enumerate(timeline):
        clip = broll_paths[clip_i]
        # Each B-roll gets its own input
        broll_scaled = tmp_dir / f"broll_dyn_{idx:02d}.mp4"
        _run(
            [
                "ffmpeg", "-y",
                "-stream_loop", "-1",
                "-i", str(clip),
                "-t", f"{dur:.3f}",
                "-vf", (
                    f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                    f"crop={CANVAS_W}:{CANVAS_H},setsar=1,fps={FPS}"
                ),
                "-an",
                "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-pix_fmt", "yuv420p",
                str(broll_scaled),
            ],
            f"scale broll {idx+1}/{n} for dynamic",
        )
        broll_inputs.append(str(broll_scaled))

    # Build a single ffmpeg command with overlay + enable
    # Inputs: [0] avatar_full, [1] broll_0, [2] broll_1, ...
    inputs_args: list[str] = ["-i", str(avatar_full)]
    for bp in broll_inputs:
        inputs_args += ["-i", bp]

    # Build filter_complex
    fc_lines = []
    prev_label = "[0:v]"

    for idx, (start_sec, dur, _) in enumerate(timeline):
        inp_idx = idx + 1  # 1-based (0 is avatar)
        end_sec = start_sec + dur
        out_label = f"[v{idx}]"
        # setpts on broll to start at 0, overlay places it at correct time
        fc_lines.append(
            f"[{inp_idx}:v]setpts=PTS-STARTPTS[b{idx}];"
        )
        fc_lines.append(
            f"{prev_label}[b{idx}]overlay=0:0:"
            f"enable='between(t,{start_sec:.3f},{end_sec:.3f})'"
            f"{out_label};"
        )
        prev_label = out_label

    # Remove trailing semicolon
    filter_str = "\n".join(fc_lines).rstrip(";")

    cmd = (
        ["ffmpeg", "-y"]
        + inputs_args
        + [
            "-filter_complex", filter_str,
            "-map", prev_label,
            "-map", "0:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path),
        ]
    )

    _run(cmd, "dynamic overlay composite", timeout=600)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
#  Layout: PRO (script-driven mixed layouts)
# ═══════════════════════════════════════════════════════════════════════════════

def _assemble_pro(
    avatar_path: Path,
    broll_paths: list[Path],
    avatar_duration: float,
    montage_plan: list[dict],
    tmp_dir: Path,
    output_path: Path,
    split_anchor_offsets: dict[int, float] | None = None,
) -> Path:
    """Script-driven pro montage with mixed layouts per segment.

    montage_plan is a list of segments:
    [
        {"start": 0.0, "end": 3.5, "layout": "split", "broll_index": 0},
        {"start": 3.5, "end": 8.0, "layout": "broll_full", "broll_index": 1},
        {"start": 8.0, "end": 12.0, "layout": "avatar_full", "broll_index": null},
        {"start": 12.0, "end": 18.0, "layout": "split", "broll_index": 2},
        ...
    ]

    Layouts:
    - "avatar_full": full-screen avatar (1080x1920)
    - "broll_full": full-screen B-roll, avatar audio continues
    - "split": 50/50 B-roll top + avatar bottom

    ``split_anchor_offsets``: map of broll index → vertical anchor float
    (0.0 = top of the Ken-Burns clip, 0.5 = centre, 1.0 = bottom). Used for
    per-photo control in product brands: most shoe photos anchor to 1.0, but
    a photo with the shoe held near the camera may want 0.5. Indexes not in
    the map fall back to 0.5 (centre — existing behaviour).
    """
    split_anchor_offsets = split_anchor_offsets or {}
    if not montage_plan:
        raise AssemblyError("Пустой монтажный план.")

    n_broll = len(broll_paths)
    AVATAR_CROP_Y = 280  # same as split mode

    # ── 1. Prepare avatar: full-screen version (with HeyGen edge overscan) ──
    avatar_full = tmp_dir / "pro_avatar_full.mp4"
    _pro_full_w = int(CANVAS_W * AVATAR_OVERSCAN)
    _pro_full_h = int(CANVAS_H * AVATAR_OVERSCAN)
    _run(
        [
            "ffmpeg", "-y", "-i", str(avatar_path),
            "-vf", (
                f"scale={_pro_full_w}:{_pro_full_h}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},setsar=1,fps={FPS}"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(avatar_full),
        ],
        "pro: scale avatar full-screen",
    )

    # ── 2. Prepare avatar: bottom-half version with overscan ──
    avatar_half = tmp_dir / "pro_avatar_half.mp4"
    _pro_half_w = int(CANVAS_W * AVATAR_OVERSCAN)
    _pro_half_h = int(HALF_H * AVATAR_OVERSCAN)
    _run(
        [
            "ffmpeg", "-y", "-i", str(avatar_path),
            "-vf", (
                f"scale={_pro_half_w}:{_pro_half_h}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{HALF_H}:(iw-{CANVAS_W})/2:min({AVATAR_CROP_Y}\\,ih-{HALF_H}),"
                f"setsar=1,fps={FPS}"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p",
            "-an",
            str(avatar_half),
        ],
        "pro: scale avatar half",
    )

    # ── 3. Prepare B-roll clips: both full-screen and half versions ──
    broll_full_clips = {}   # index -> path (1080x1920)
    broll_half_clips = {}   # index -> path (1080x960)

    needed_full = {}   # bi -> max duration needed
    needed_half = {}   # bi -> max duration needed
    for seg in montage_plan:
        bi = seg.get("broll_index")
        if bi is not None and bi < n_broll:
            seg_dur = seg["end"] - seg["start"]
            if seg["layout"] == "broll_full":
                needed_full[bi] = max(needed_full.get(bi, 0), seg_dur)
            elif seg["layout"] == "split":
                needed_half[bi] = max(needed_half.get(bi, 0), seg_dur)
            elif seg["layout"] == "pip":
                # floating: B-roll занимает весь экран (как broll_full),
                # аватар сверху круглым PiP — фон готовится full-screen.
                needed_full[bi] = max(needed_full.get(bi, 0), seg_dur)

    for bi, max_dur in needed_full.items():
        clip = broll_paths[bi]
        out = tmp_dir / f"pro_broll_full_{bi:02d}.mp4"
        # Only prepare enough duration (+ 2s buffer) instead of full avatar length
        prep_dur = min(max_dur + 2.0, avatar_duration)

        # Detect aspect ratio: if landscape → blur-bg pillarbox, else → crop
        try:
            probe_cmd = [
                "ffprobe", "-v", "quiet", "-show_entries", "stream=width,height",
                "-select_streams", "v:0", "-of", "csv=p=0", str(clip),
            ]
            probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
            src_w, src_h = [int(x) for x in probe_res.stdout.strip().split(",")[:2]]
            is_landscape = src_w > src_h
        except Exception:
            is_landscape = False

        if is_landscape:
            # Blur-background: blurred scaled bg + sharp centered foreground
            vf = (
                f"split[bg][fg];"
                f"[bg]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},gblur=sigma=30[blurred];"
                f"[fg]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{CANVAS_H}:(ow-iw)/2:(oh-ih)/2:color=black@0[sharp];"
                f"[blurred][sharp]overlay=0:0,setsar=1,fps={FPS}"
            )
        else:
            vf = (
                f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},setsar=1,fps={FPS}"
            )

        _run(
            [
                "ffmpeg", "-y",
                "-i", str(clip),
                "-t", f"{prep_dur:.1f}",
                "-vf", vf,
                "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-pix_fmt", "yuv420p",
                str(out),
            ],
            f"pro: scale broll {bi} full-screen {'(blur-bg)' if is_landscape else '(crop)'} ({prep_dur:.0f}s)",
            timeout=600,
        )
        broll_full_clips[bi] = out

    for bi, max_dur in needed_half.items():
        clip = broll_paths[bi]
        out = tmp_dir / f"pro_broll_half_{bi:02d}.mp4"
        prep_dur = min(max_dur + 2.0, avatar_duration)

        # Split crop strategy — parametric y-anchor (0.0 top … 1.0 bottom).
        #   0.5 (default) preserves the old centre-crop behaviour.
        #   1.0 is the shoe-brand default: lower 960px of the 1080×1920
        #       Ken-Burns clip, so full-body product shots keep shoes in slot.
        #   Intermediate values give per-photo tuning (see _resolve_photo_anchor).
        anchor = split_anchor_offsets.get(bi, 0.5)
        # Landscape-aware split: if the source B-roll is wider than tall
        # (demo videos, UI recordings, screencasts — the 95% case) the old
        # `increase+crop` path would slice 300+px off each side, hiding
        # UI/text/graphs at the frame edges. Fit-with-blur-bg preserves
        # the entire landscape frame inside the 1080×960 slot.
        try:
            _probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "stream=width,height",
                 "-select_streams", "v:0", "-of", "csv=p=0", str(clip)],
                capture_output=True, text=True, timeout=10,
            )
            _sw, _sh = [int(x) for x in _probe.stdout.strip().split(",")[:2]]
            _is_landscape = _sw > _sh
        except Exception:
            _is_landscape = False

        if abs(anchor - 0.5) < 0.01 and _is_landscape:
            # Landscape → blur-bg pillarbox in the half-screen slot.
            vf = (
                f"split[bg][fg];"
                f"[bg]scale={CANVAS_W}:{HALF_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{HALF_H},gblur=sigma=30[blurred];"
                f"[fg]scale={CANVAS_W}:{HALF_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{HALF_H}:(ow-iw)/2:(oh-ih)/2:color=black@0[sharp];"
                f"[blurred][sharp]overlay=0:0,setsar=1,fps={FPS}"
            )
            tag = "half (blur-bg landscape)"
        elif abs(anchor - 0.5) < 0.01:
            # Portrait/square → centre-crop fast path (old default).
            vf = (
                f"scale={CANVAS_W}:{HALF_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{HALF_H},setsar=1,fps={FPS}"
            )
            tag = "half"
        else:
            # Scale into 1080×1920, then crop a 1080×960 band whose top
            # edge is at y = anchor * (CANVAS_H - HALF_H).
            # anchor=0.0 → y=0 (top), anchor=1.0 → y=960 (bottom).
            y_offset = int(round(anchor * (CANVAS_H - HALF_H)))
            vf = (
                f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H}:(iw-{CANVAS_W})/2:0,"
                f"crop={CANVAS_W}:{HALF_H}:0:{y_offset},setsar=1,fps={FPS}"
            )
            tag = f"half (anchor={anchor:.2f})"

        _run(
            [
                "ffmpeg", "-y",
                "-i", str(clip),
                "-t", f"{prep_dur:.1f}",
                "-vf", vf,
                "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-pix_fmt", "yuv420p",
                str(out),
            ],
            f"pro: scale broll {bi} {tag} ({prep_dur:.0f}s)",
            timeout=600,
        )
        broll_half_clips[bi] = out

    # ── 4. Build segments as individual clips, then concat ──
    segment_files = []
    for si, seg in enumerate(montage_plan):
        start = seg["start"]
        end = seg["end"]
        dur = end - start
        if dur <= 0:
            continue

        layout = seg["layout"]
        bi = seg.get("broll_index")
        seg_out = tmp_dir / f"pro_seg_{si:02d}.mp4"

        if layout == "avatar_full":
            # Cut avatar segment
            _run(
                [
                    "ffmpeg", "-y",
                    "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-i", str(avatar_full),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    str(seg_out),
                ],
                f"pro: seg {si} avatar_full ({dur:.1f}s)",
            )

        elif layout == "broll_full" and bi is not None and bi in broll_full_clips:
            # Full-screen B-roll with avatar audio underneath
            # B-roll starts from 0 (not time-synced), avatar at correct position for audio
            broll_clip = broll_full_clips[bi]
            _run(
                [
                    "ffmpeg", "-y",
                    "-t", f"{dur:.3f}",
                    "-i", str(broll_clip),
                    "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-i", str(avatar_full),
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",
                    str(seg_out),
                ],
                f"pro: seg {si} broll_full #{bi} ({dur:.1f}s)",
            )

        elif layout == "split" and bi is not None and bi in broll_half_clips:
            # 50/50 split: B-roll top (from 0) + avatar bottom (at position for audio)
            broll_clip = broll_half_clips[bi]
            _run(
                [
                    "ffmpeg", "-y",
                    "-t", f"{dur:.3f}",
                    "-i", str(broll_clip),
                    "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-i", str(avatar_half),
                    "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-i", str(avatar_full),
                    "-filter_complex", "[0:v][1:v]vstack=inputs=2[outv]",
                    "-map", "[outv]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",
                    str(seg_out),
                ],
                f"pro: seg {si} split #{bi} ({dur:.1f}s)",
            )

        elif layout == "pip" and bi is not None and bi in broll_full_clips:
            # Floating avatar: B-roll на весь экран + круглый talking-head PiP
            # в углу (снизу-справа). Круг через geq alpha-маску. Аудио аватара.
            broll_clip = broll_full_clips[bi]
            _px, _py = _pip_overlay_position(
                CANVAS_W, CANVAS_H, PIP_DIAMETER, PIP_MARGIN, PIP_CORNER
            )
            _r = PIP_DIAMETER / 2.0
            _fc = (
                f"[1:v]scale={PIP_DIAMETER}:{PIP_DIAMETER}:force_original_aspect_ratio=increase,"
                f"crop={PIP_DIAMETER}:{PIP_DIAMETER},format=rgba,"
                f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='if(lte(hypot(X-{_r:.1f},Y-{_r:.1f}),{_r:.1f}),255,0)'[pip];"
                f"[0:v][pip]overlay={_px}:{_py}:format=auto[outv]"
            )
            _run(
                [
                    "ffmpeg", "-y",
                    "-t", f"{dur:.3f}",
                    "-i", str(broll_clip),
                    "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-i", str(avatar_full),
                    "-filter_complex", _fc,
                    "-map", "[outv]", "-map", "1:a",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",
                    str(seg_out),
                ],
                f"pro: seg {si} pip #{bi} ({dur:.1f}s)",
            )

        else:
            # Fallback: avatar full
            _run(
                [
                    "ffmpeg", "-y",
                    "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-i", str(avatar_full),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    str(seg_out),
                ],
                f"pro: seg {si} fallback avatar ({dur:.1f}s)",
            )

        if seg_out.exists() and seg_out.stat().st_size > 0:
            segment_files.append(seg_out)

    if not segment_files:
        raise AssemblyError("Ни один сегмент не был собран.")

    # ── 5. Concat all segments ──
    concat_list = tmp_dir / "pro_concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{seg.name}'" for seg in segment_files),
        encoding="utf-8",
    )

    # Use concat demuxer (fast, no re-encode)
    _run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ],
        f"pro: concat {len(segment_files)} segments",
    )

    logger.info(
        f"[assembler] pro montage: {len(segment_files)} segments, "
        f"{avatar_duration:.1f}s total"
    )
    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════════

def assemble_auto_montage(
    project_dir: Path,
    layout: str = "split",
    subtitles: bool = False,
    subtitle_language: str = "ru",
    montage_plan: list[dict] | None = None,
    brand_name: str = "default",
    smart_mix_cfg: dict | None = None,
) -> Path:
    """Build a 9:16 video from avatar + B-roll.

    Parameters
    ----------
    project_dir : Path
        Folder containing ``avatar_*.mp4`` and ``broll_*.mp4`` files.
        Optionally ``photos/`` subfolder or ``photo_*.*`` in the root for
        mixed-source layouts.
    layout : str
        - ``"split"`` — 50/50 top-bottom (B-roll over avatar)
        - ``"dynamic"`` — full-screen alternating (B-roll covers avatar)
        - ``"pro"`` — script-driven mixed layouts via Opus-generated
          ``montage_plan``
        - ``"smart"`` — deterministic mixed plan built in code: videos play
          full clip length as broll_full, photos play 2.8s as split. Ideal
          when the project has both video and photo material and you want
          cuts to land exactly on clip boundaries ("премиум-монтаж без
          рваных переходов").
    subtitles : bool
        If True, burn word-by-word animated subtitles (CapCut style).
    subtitle_language : str
        Language code for Whisper transcription (default ``"ru"``).
    brand_name : str
        Brand profile name (e.g. "shoes", "default"). Currently controls
        which Ken Burns variant is used for photos in the ``smart`` layout —
        "shoes" uses ``zoom_in_shoes`` (anchored to the bottom of the frame)
        so product shots with full-body models don't lose the shoes when
        the clip lands in a split slot.

    Returns
    -------
    Path
        Path to the output video (``final_auto.mp4`` or ``final_auto_subs.mp4``).
    """
    project_dir = Path(project_dir)
    if not project_dir.exists():
        raise AssemblyError(f"Проект не найден: {project_dir}")

    avatar_path = _find_avatar(project_dir)
    video_paths = _find_broll(project_dir)
    project_photos = _find_project_photos(project_dir)

    avatar_duration = _probe_duration(avatar_path)
    if avatar_duration < 1:
        raise AssemblyError(f"Аватар слишком короткий ({avatar_duration:.1f}с)")

    tmp_dir = project_dir / "_tmp_montage"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()

    # ── Smart layout: deterministic mixed plan ──────────────────────────────
    # Videos play full clip length as broll_full (no mid-clip cuts).
    # Photos play photo_clip_dur seconds each as split (avatar stays visible).
    # Plan is built in code, NOT via Opus — so the assembler knows exactly
    # which broll_index is video and which is photo.
    #
    # Per-brand smart_mix config (intro/outro/photo dur) — passed via the
    # `smart_mix_cfg` kwarg from the bot. Defaults are the legacy values
    # (1.5/2.0/2.8) for backwards compatibility.
    used_smart = False
    smart_anchor_offsets: dict[int, float] = {}
    if layout == "smart":
        cfg = smart_mix_cfg or {}
        smart_intro_dur = float(cfg.get("intro_dur", 1.5))
        smart_outro_dur = float(cfg.get("outro_dur", 2.0))
        photo_dur_default = float(cfg.get("photo_dur_default", 2.8))
        photo_dur_min = float(cfg.get("photo_dur_min", photo_dur_default))
        photo_dur_max = float(cfg.get("photo_dur_max", photo_dur_default))

        # Compute optimal photo_clip_dur:
        # ideal = (avatar - intro - outro) / N, clamped to [min, max].
        # This keeps rhythm comfortable regardless of how many photos.
        n_photos = len(project_photos) if project_photos else 0
        if n_photos > 0 and photo_dur_min < photo_dur_max:
            active_window = max(0.0, avatar_duration - smart_intro_dur - smart_outro_dur)
            ideal = active_window / n_photos if n_photos else photo_dur_default
            photo_clip_dur = max(photo_dur_min, min(photo_dur_max, ideal))
            logger.info(
                f"[assembler] smart photo_dur dynamic: N={n_photos}, "
                f"active={active_window:.1f}s, ideal={ideal:.2f}s, "
                f"clamped=[{photo_dur_min:.2f}..{photo_dur_max:.2f}] → {photo_clip_dur:.2f}s"
            )
        else:
            photo_clip_dur = photo_dur_default

        # Brand-aware Ken Burns variant selection.
        # "shoes" → all photos anchored to bottom (subject at frame bottom).
        # Other brands → rotate through the default set.
        if brand_name == "shoes":
            smart_variants = ["zoom_in_shoes"]
        else:
            smart_variants = None
        photo_clips = (
            _build_ken_burns_clips(
                project_photos, tmp_dir, photo_clip_dur,
                name_prefix="smart_photo",
                variants=smart_variants,
            ) if project_photos else []
        )
        if not video_paths and not photo_clips:
            raise AssemblyError(
                "Smart-микс требует хотя бы одно видео (broll_*.mp4) "
                "или фото (photos/ или photo_*.*) в папке проекта."
            )
        broll_paths = list(video_paths) + photo_clips
        # Per-photo split anchor. For the shoes brand the default is 0.75
        # (lower three-quarters of the photo — empirical balance for lifestyle
        # shots where the model sits and shoes sit around 60-80% of the frame,
        # not glued to the very bottom). Was 1.0 — too aggressive: product
        # lifestyle photos have floor/background at the bottom, which was
        # hogging the slot. Each photo can override via filename suffix
        # (_top / _center / _bottom) or a <photo>.anchor.txt sidecar — see
        # _resolve_photo_anchor.
        # Other brands keep the old centre-crop behaviour (anchor 0.5).
        smart_anchor_offsets: dict[int, float] = {}
        if brand_name == "shoes" and project_photos and photo_clips:
            # 7 июня 2026: anchor выверен геометрически (_split_visible_photo_band
            # + TDD). Обувь lifestyle лежит в нижних [0.60,0.95] фото; чтобы она
            # попадала в split-слот ЦЕЛИКОМ на всех кадрах Ken Burns, нужен
            # высокий anchor (низ фото). 0.75 и 0.62 срезали обувь снизу.
            brand_default_anchor = SHOES_SPLIT_ANCHOR
            for local_i, photo_path in enumerate(project_photos):
                global_i = len(video_paths) + local_i
                # Guard: project_photos and photo_clips are 1:1 by index,
                # but _build_ken_burns_clips may drop a clip if ffmpeg fails.
                # If counts diverge we stop — smart layout won't be asked in
                # that state because fewer photo_clips would have been built.
                if local_i >= len(photo_clips):
                    break
                smart_anchor_offsets[global_i] = _resolve_photo_anchor(
                    photo_path, brand_default=brand_default_anchor
                )
            logger.info(
                f"[assembler] smart anchors (shoes): "
                f"{[(i, round(v, 2)) for i, v in smart_anchor_offsets.items()]}"
            )
        montage_plan = _plan_smart_mixed_montage(
            video_paths, photo_clips, photo_clip_dur, avatar_duration,
            intro_dur=smart_intro_dur, outro_dur=smart_outro_dur,
        )
        used_smart = True
        # Smart routes through the pro pipeline (same segment assembler).
        layout = "pro"

    # ── Fullscreen-only layout ──────────────────────────────────────────────
    # Все B-roll (видео + фото с лёгким Ken Burns) подряд на полный экран.
    # Avatar показывается ТОЛЬКО в intro и outro CTA, без чередования в
    # середине. Создан 5 мая 2026 для shoes lifestyle-фото 9:16 где split
    # обрезает важное (модель + обувь не помещаются в 1080×960 верхнюю
    # половину).
    # ── FLOATING (плавающий аватар PiP) ─────────────────────────────────────
    # Аватар на весь экран в начале (hook) и конце (CTA), в середине — B-roll
    # на весь экран + круглый talking-head PiP в углу. Запрос Артёма 7 июня
    # 2026 (референс x5.mov). B-roll готовится full-screen (как fullscreen),
    # отличие — body-сегменты layout 'pip' и план _plan_floating_montage.
    used_floating = False
    if layout == "floating":
        cfg = smart_mix_cfg or {}
        fl_intro_dur = float(cfg.get("floating_intro_dur", 5.0))
        fl_outro_dur = float(cfg.get("floating_outro_dur", 4.0))
        fl_photo_dur_min = float(cfg.get("photo_dur_min", 1.5))
        fl_photo_dur_max = float(cfg.get("photo_dur_max", 3.5))
        fl_photo_dur_default = float(cfg.get("photo_dur_default", 2.5))

        n_photos = len(project_photos) if project_photos else 0
        active_window = max(0.0, avatar_duration - fl_intro_dur - fl_outro_dur)
        video_total = sum(_probe_duration(p) for p in video_paths) if video_paths else 0.0
        budget_for_photos = max(0.0, active_window - video_total)
        if n_photos > 0:
            ideal = budget_for_photos / n_photos
            photo_clip_dur = max(fl_photo_dur_min, min(fl_photo_dur_max, ideal))
        else:
            photo_clip_dur = fl_photo_dur_default

        # Ken Burns на full-screen фон (shoes → bottom-anchor, как fullscreen).
        if brand_name == "shoes":
            fl_variants = ["zoom_in_shoes_full"]
        else:
            fl_variants = None
        photo_clips = (
            _build_ken_burns_clips(
                project_photos, tmp_dir, photo_clip_dur,
                name_prefix="fl_photo", variants=fl_variants,
            ) if project_photos else []
        )
        if not video_paths and not photo_clips:
            raise AssemblyError(
                "Floating режим требует хотя бы одно видео или фото в проекте."
            )
        broll_paths = list(video_paths) + photo_clips
        montage_plan = _plan_floating_montage(
            video_paths, photo_clips, photo_clip_dur, avatar_duration,
            intro_dur=fl_intro_dur, outro_dur=fl_outro_dur,
        )
        used_floating = True
        layout = "pro"  # роутится через тот же сегмент-ассемблер

    used_fullscreen = False
    if layout == "fullscreen":
        cfg = smart_mix_cfg or {}
        fs_intro_dur = float(cfg.get("intro_dur", 2.0))
        fs_outro_dur = float(cfg.get("outro_dur", 3.0))
        # Для full-screen фото может играть чуть быстрее (полный экран
        # удерживает внимание дольше), shrink-range шире чем у smart-mix.
        photo_dur_min = float(cfg.get("photo_dur_min", 1.5))
        photo_dur_max = float(cfg.get("photo_dur_max", 3.5))
        photo_dur_default = float(cfg.get("photo_dur_default", 2.5))

        # Динамический photo_clip_dur: считаем budget после видео.
        n_photos = len(project_photos) if project_photos else 0
        active_window = max(0.0, avatar_duration - fs_intro_dur - fs_outro_dur)
        video_total = sum(_probe_duration(p) for p in video_paths) if video_paths else 0.0
        budget_for_photos = max(0.0, active_window - video_total)

        if n_photos > 0:
            ideal = budget_for_photos / n_photos
            photo_clip_dur = max(photo_dur_min, min(photo_dur_max, ideal))
            logger.info(
                f"[assembler] fullscreen photo_dur: N={n_photos}, "
                f"active={active_window:.1f}s, video_total={video_total:.1f}s, "
                f"budget={budget_for_photos:.1f}s, ideal={ideal:.2f}s, "
                f"clamped=[{photo_dur_min:.2f}..{photo_dur_max:.2f}] → "
                f"{photo_clip_dur:.2f}s"
            )
        else:
            photo_clip_dur = photo_dur_default

        # Ken Burns на full-screen с brand-aware anchor.
        # Для shoes — `zoom_in_shoes_full` (bottom-anchor): обувь в нижней
        # части фото остаётся в кадре весь клип (без bottom-anchor дефолтный
        # центр-зум 1.15× выгоняет обувь из кадра — было замечено на эталоне
        # #2 «Лоферы рождаются под тебя» 5 мая 2026).
        # Other brands — default rotation (zoom_in / left / right / up).
        if brand_name == "shoes":
            fs_variants = ["zoom_in_shoes_full"]
        else:
            fs_variants = None
        photo_clips = (
            _build_ken_burns_clips(
                project_photos, tmp_dir, photo_clip_dur,
                name_prefix="fs_photo",
                variants=fs_variants,
            ) if project_photos else []
        )
        if not video_paths and not photo_clips:
            raise AssemblyError(
                "Full-screen режим требует хотя бы одно видео или фото в проекте."
            )

        broll_paths = list(video_paths) + photo_clips
        montage_plan = _plan_fullscreen_only_montage(
            video_paths, photo_clips, photo_clip_dur, avatar_duration,
            intro_dur=fs_intro_dur, outro_dur=fs_outro_dur,
        )
        used_fullscreen = True
        # Fullscreen routes through pro pipeline (same segment assembler).
        layout = "pro"

    # ── Project-photo MIX (for non-smart layouts) ───────────────────────────
    # If the project has its own photos AND we're not in smart mode, turn
    # them into Ken Burns clips and shuffle them INTO the video B-roll list.
    # This gives a lightly-mixed montage for split/dynamic/pro layouts
    # without the smart-mode's strict video-full-photo-split rule.
    broll_paths = broll_paths if (used_smart or used_fullscreen or used_floating) else list(video_paths)
    used_photo_mix = False
    if project_photos and not used_smart and not used_fullscreen and not used_floating:
        photo_clip_dur = 2.8
        logger.info(
            f"[assembler] mixing in {len(project_photos)} project-photo(s) "
            f"as Ken Burns clips ({photo_clip_dur:.1f}s each)"
        )
        photo_clips = _build_ken_burns_clips(
            project_photos, tmp_dir, photo_clip_dur, name_prefix="project_photo"
        )
        if photo_clips:
            broll_paths = list(broll_paths) + photo_clips
            # Shuffle so videos and photos interleave throughout the montage
            # rather than all videos first, then all photos at the tail.
            import random
            random.shuffle(broll_paths)
            used_photo_mix = True

    # ── Library-photo fallback ──────────────────────────────────────────────
    # If we still have nothing (no project videos AND no project photos),
    # fall back to the curated photo library (broll-library/photos/**).
    used_photo_fallback = False
    if not broll_paths:
        # Short-form rhythm: a static photo held for 5s reads as dead air on
        # reels/shorts. We aim for ~2.8s per Ken Burns clip (≈2.2s of active
        # motion after fade in/out) and lots of them — on a 60s avatar that's
        # ~21 cuts, matching how Artem actually hand-cuts his videos.
        # Clamp [8, 20] so we never ask for fewer than 8 (pacing floor) or
        # more than 20 (beyond 20 the library starts looking repetitive).
        photo_count = max(8, min(20, int(round(avatar_duration / 2.8))))
        photo_clip_dur = 2.8
        logger.info(
            f"[assembler] no project clips — trying library photo fallback "
            f"({photo_count} clips × {photo_clip_dur:.1f}s, avatar={avatar_duration:.1f}s)"
        )
        broll_paths = _gather_photo_broll(
            project_dir, photo_count, photo_clip_dur, tmp_dir
        )
        used_photo_fallback = bool(broll_paths)

    if not broll_paths:
        raise AssemblyError(
            "Нет B-roll клипов в папке проекта и пустая библиотека фото. "
            "Сначала выбери и сохрани B-roll кнопкой «💾 Сохранить выбранные "
            "в Notion», скинь видео в чат «🎬 Скинуть видео для нарезки», "
            "положи фото в projects/<id>/photos/ или закинь картинки в "
            "broll-library/photos/."
        )

    mix_tag = ""
    if used_smart:
        mix_tag = (
            f" (smart: {len(video_paths)} video-full + "
            f"{len(project_photos)} photo-split)"
        )
    elif used_photo_mix:
        mix_tag = f" (+{len(project_photos)} project photos mixed)"
    elif used_photo_fallback:
        mix_tag = " (library-photo fallback)"
    logger.info(
        f"[assembler] avatar={avatar_path.name} ({avatar_duration:.1f}с), "
        f"{len(broll_paths)} broll clips{mix_tag}, layout={layout}"
    )

    final_out = project_dir / "final_auto.mp4"

    try:
        if layout == "pro" and montage_plan:
            _assemble_pro(
                avatar_path, broll_paths, avatar_duration,
                montage_plan, tmp_dir, final_out,
                split_anchor_offsets=smart_anchor_offsets,
            )
        elif layout == "dynamic":
            _assemble_dynamic(
                avatar_path, broll_paths, avatar_duration,
                tmp_dir, final_out,
            )
        else:
            _assemble_split(
                avatar_path, broll_paths, avatar_duration,
                tmp_dir, final_out,
            )

        final_size = final_out.stat().st_size / 1024 / 1024
        logger.info(
            f"[assembler] ✅ Montage done: {final_out.name} "
            f"({final_size:.1f} MB, {avatar_duration:.1f}с, layout={layout})"
        )

        # ── Optional subtitles ──
        if subtitles:
            try:
                from subtitle_burner import add_subtitles_to_video

                font_dir_path = FONT_DIR if FONT_DIR.exists() else None
                subs_out = project_dir / "final_auto_subs.mp4"

                # Subtitle position depends on layout:
                # - split: MarginV=900 (at junction broll/avatar)
                # - pro: adaptive per segment (split→900, avatar/broll→480)
                # - dynamic/other: MarginV=480 (lower)
                if layout == "split":
                    sub_margin_v = 900
                    sub_plan = None
                elif layout == "pro" and montage_plan:
                    sub_margin_v = 480  # default for non-split segments
                    sub_plan = montage_plan
                else:
                    sub_margin_v = 480
                    sub_plan = None

                logger.info(f"[assembler] Adding subtitles (margin_v={sub_margin_v}, adaptive={sub_plan is not None}) …")
                result = add_subtitles_to_video(
                    video_path=final_out,
                    output_path=subs_out,
                    language=subtitle_language,
                    font_dir=font_dir_path,
                    uppercase=True,
                    margin_v=sub_margin_v,
                    montage_plan=sub_plan,
                )
                subs_size = result.stat().st_size / 1024 / 1024
                logger.info(
                    f"[assembler] ✅ Subtitles added: {result.name} ({subs_size:.1f} MB)"
                )
                return result

            except Exception as e:
                logger.error(f"[assembler] Subtitle burn failed, returning video without subs: {e}")
                # Fall through: return the video without subtitles

        return final_out

    finally:
        try:
            shutil.rmtree(tmp_dir)
        except Exception as e:
            logger.warning(f"[assembler] cleanup failed: {e}")
