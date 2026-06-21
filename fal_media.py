"""
fal.ai media generation module — Nano Banana Pro (image) + Kling 3.0 Pro (video).

Public API:
    generate_image(prompt, aspect="9:16")       -> str | None   (path to PNG)
    generate_video(prompt, duration=5,          -> str | None   (path to MP4)
                   aspect="9:16")
    generate_video_from_image(prompt, image,    -> str | None   (path to MP4)
                              duration=5)
    generate_seedance_video(prompt, dest,        -> str | None   (path to MP4)
                            duration=5, aspect="9:16")

Env:
    FAL_KEY — required; if missing, every call returns None (caller decides fallback).

Pricing (audio-off, 2026-04-24):
    Nano Banana Pro       — $0.15 / image
    Kling 3.0 Pro T2V 5s  — $0.56
    Kling 3.0 Pro T2V 10s — $1.12
    Kling 3.0 Pro I2V 5s  — $0.56
    Kling 3.0 Pro I2V 10s — $1.12

Outputs land in content-bot-2/media/fal/<date>/<timestamp>_<kind>.<ext>.
Never logs full FAL_KEY — only masks it. Failures logged + None returned;
callers must handle None gracefully (don't crash user flow).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# --- Config ---------------------------------------------------------------

MEDIA_ROOT = Path(__file__).parent / "media" / "fal"
IMAGE_ENDPOINT = "fal-ai/nano-banana-pro"
VIDEO_T2V_ENDPOINT = "fal-ai/kling-video/v3/pro/text-to-video"
VIDEO_I2V_ENDPOINT = "fal-ai/kling-video/v3/pro/image-to-video"
SEEDANCE_T2V_ENDPOINT = "fal-ai/bytedance/seedance/v1/pro/fast/text-to-video"

SUPPORTED_DURATIONS = (5, 10)
SUPPORTED_ASPECTS = ("9:16", "16:9", "1:1")
SEEDANCE_DURATIONS = (5, 10)        # user-facing subset (model accepts 2-12)
SEEDANCE_RESOLUTION = "720p"        # default: cheaper (~4/9 of 1080p, token-priced); montage upscales to 1080
SEEDANCE_TIMEOUT_S = 900            # hard deadline for fal subscribe (paid cloud call must not hang)
SEEDANCE_DOWNLOAD_TIMEOUT_S = 120   # socket timeout for clip download
SEEDANCE_MIN_BYTES = 50_000         # below this the "mp4" is an error page / truncated → reject

Duration = Literal[5, 10]
Aspect = Literal["9:16", "16:9", "1:1"]


def _is_configured() -> bool:
    key = os.getenv("FAL_KEY", "").strip()
    if not key or ":" not in key:
        return False
    return True


def _safe_key_preview() -> str:
    key = os.getenv("FAL_KEY", "")
    if not key:
        return "<missing>"
    head = key.split(":", 1)[0][:8]
    return f"{head}…"


def _out_path(kind: str, ext: str) -> Path:
    date = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H%M%S_%f")[:-3]
    folder = MEDIA_ROOT / date
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{ts}_{kind}.{ext}"


def _download(url: str, dest: Path) -> Path:
    urllib.request.urlretrieve(url, dest)
    return dest


def seedance_ready() -> "tuple[bool, str]":
    """Cheap preflight: is Seedance callable right now? (FAL_KEY + fal_client).

    Lets the engine fail fast with a clear reason BEFORE spending the Claude
    director call.
    """
    if not _is_configured():
        return False, "FAL_KEY not configured"
    try:
        import fal_client  # noqa: F401
    except ImportError:
        return False, "fal-client not installed"
    return True, ""


def _download_timeout(url: str, dest: "str | Path") -> None:
    """Download url → dest with connect/read timeout (Seedance is paid — never hang)."""
    with urllib.request.urlopen(url, timeout=SEEDANCE_DOWNLOAD_TIMEOUT_S) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


# --- Public API -----------------------------------------------------------

def generate_image(
    prompt: str,
    aspect: Aspect = "9:16",
) -> Optional[str]:
    """Generate one image via Nano Banana Pro. Returns local PNG path or None."""
    if not _is_configured():
        logger.warning("fal_media.generate_image: FAL_KEY missing, skipping")
        return None
    if aspect not in SUPPORTED_ASPECTS:
        logger.error(f"fal_media.generate_image: bad aspect {aspect!r}")
        return None

    try:
        import fal_client
    except ImportError:
        logger.error("fal_media.generate_image: fal-client not installed")
        return None

    t0 = time.time()
    logger.info(f"fal_media: image {aspect} prompt={prompt[:80]!r} key={_safe_key_preview()}")

    try:
        result = fal_client.subscribe(
            IMAGE_ENDPOINT,
            arguments={"prompt": prompt, "aspect_ratio": aspect},
            with_logs=False,
        )
    except Exception as e:
        logger.error(f"fal_media.generate_image: API error: {type(e).__name__}: {str(e)[:200]}")
        return None

    images = result.get("images") or []
    if not images:
        logger.error(f"fal_media.generate_image: no images in result: {result!r}")
        return None

    url = images[0].get("url") if isinstance(images[0], dict) else images[0]
    if not url:
        logger.error("fal_media.generate_image: no url in image item")
        return None

    dest = _out_path("nbp_image", "png")
    try:
        _download(url, dest)
    except Exception as e:
        logger.error(f"fal_media.generate_image: download failed: {e}")
        return None

    logger.info(f"fal_media: image ready in {time.time()-t0:.1f}s → {dest}")
    return str(dest)


def generate_video(
    prompt: str,
    duration: Duration = 5,
    aspect: Aspect = "9:16",
) -> Optional[str]:
    """Generate video via Kling 3.0 Pro text-to-video. Returns local MP4 path or None."""
    if not _is_configured():
        logger.warning("fal_media.generate_video: FAL_KEY missing, skipping")
        return None
    if duration not in SUPPORTED_DURATIONS:
        logger.error(f"fal_media.generate_video: bad duration {duration!r} (supported: {SUPPORTED_DURATIONS})")
        return None
    if aspect not in SUPPORTED_ASPECTS:
        logger.error(f"fal_media.generate_video: bad aspect {aspect!r}")
        return None

    try:
        import fal_client
    except ImportError:
        logger.error("fal_media.generate_video: fal-client not installed")
        return None

    t0 = time.time()
    logger.info(
        f"fal_media: video {duration}s {aspect} prompt={prompt[:80]!r} "
        f"key={_safe_key_preview()}"
    )

    try:
        result = fal_client.subscribe(
            VIDEO_T2V_ENDPOINT,
            arguments={
                "prompt": prompt,
                "duration": str(duration),
                "aspect_ratio": aspect,
            },
            with_logs=False,
        )
    except Exception as e:
        logger.error(f"fal_media.generate_video: API error: {type(e).__name__}: {str(e)[:200]}")
        return None

    video = result.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        logger.error(f"fal_media.generate_video: no video.url in result: {result!r}")
        return None

    dest = _out_path(f"kling_t2v_{duration}s", "mp4")
    try:
        _download(url, dest)
    except Exception as e:
        logger.error(f"fal_media.generate_video: download failed: {e}")
        return None

    logger.info(f"fal_media: video ready in {time.time()-t0:.1f}s → {dest}")
    return str(dest)


def generate_video_from_image(
    prompt: str,
    image_url_or_path: str,
    duration: Duration = 5,
) -> Optional[str]:
    """Animate a still image via Kling 3.0 Pro image-to-video.

    image_url_or_path — either https:// URL or local file path (will be uploaded to fal).
    """
    if not _is_configured():
        logger.warning("fal_media.generate_video_from_image: FAL_KEY missing")
        return None
    if duration not in SUPPORTED_DURATIONS:
        logger.error(f"fal_media.generate_video_from_image: bad duration {duration!r}")
        return None

    try:
        import fal_client
    except ImportError:
        logger.error("fal_media.generate_video_from_image: fal-client not installed")
        return None

    # Upload local file if needed
    if image_url_or_path.lower().startswith(("http://", "https://")):
        image_url = image_url_or_path
    else:
        try:
            image_url = fal_client.upload_file(image_url_or_path)
        except Exception as e:
            logger.error(f"fal_media: upload_file failed: {e}")
            return None

    t0 = time.time()
    logger.info(f"fal_media: i2v {duration}s prompt={prompt[:80]!r}")

    try:
        result = fal_client.subscribe(
            VIDEO_I2V_ENDPOINT,
            arguments={
                "prompt": prompt,
                "image_url": image_url,
                "duration": str(duration),
            },
            with_logs=False,
        )
    except Exception as e:
        logger.error(f"fal_media.generate_video_from_image: API error: {type(e).__name__}: {str(e)[:200]}")
        return None

    video = result.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        logger.error(f"fal_media.generate_video_from_image: no video.url: {result!r}")
        return None

    dest = _out_path(f"kling_i2v_{duration}s", "mp4")
    try:
        _download(url, dest)
    except Exception as e:
        logger.error(f"fal_media: download failed: {e}")
        return None

    logger.info(f"fal_media: i2v ready in {time.time()-t0:.1f}s → {dest}")
    return str(dest)


def generate_seedance_video(
    prompt: str,
    dest: "str | Path",
    duration: Duration = 5,
    aspect: Aspect = "9:16",
) -> Optional[str]:
    """Generate one cinematic clip via ByteDance Seedance Pro Fast (text-to-video).

    Unlike generate_video (Kling), the caller passes `dest` — the engine owns
    the output namespace (e.g. proj/aivideo/ai_01.mp4). Returns the path str or
    None on any failure (callers must handle None gracefully — see module docstring).
    """
    if not _is_configured():
        logger.warning("fal_media.generate_seedance_video: FAL_KEY missing, skipping")
        return None
    if duration not in SEEDANCE_DURATIONS:
        logger.error(f"fal_media.generate_seedance_video: bad duration {duration!r} (supported: {SEEDANCE_DURATIONS})")
        return None
    if aspect not in SUPPORTED_ASPECTS:
        logger.error(f"fal_media.generate_seedance_video: bad aspect {aspect!r}")
        return None

    try:
        import fal_client
    except ImportError:
        logger.error("fal_media.generate_seedance_video: fal-client not installed")
        return None

    dest = Path(dest)
    t0 = time.time()
    logger.info(
        f"fal_media: seedance {duration}s {aspect} prompt={prompt[:80]!r} "
        f"key={_safe_key_preview()}"
    )

    try:
        result = fal_client.subscribe(
            SEEDANCE_T2V_ENDPOINT,
            arguments={
                "prompt": prompt,
                "duration": str(duration),
                "resolution": SEEDANCE_RESOLUTION,
                "aspect_ratio": aspect,
            },
            with_logs=False,
            start_timeout=SEEDANCE_TIMEOUT_S,
            client_timeout=SEEDANCE_TIMEOUT_S,
        )
    except Exception as e:
        logger.error(f"fal_media.generate_seedance_video: API error: {type(e).__name__}: {str(e)[:200]}")
        return None

    video = result.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        logger.error(f"fal_media.generate_seedance_video: no video.url in result: {result!r}")
        return None

    # Atomic + validated: download to .part, reject tiny/broken (error pages), then rename.
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        _download_timeout(url, part)
    except Exception as e:
        logger.error(f"fal_media.generate_seedance_video: download failed: {e}")
        part.unlink(missing_ok=True)
        return None
    if part.stat().st_size < SEEDANCE_MIN_BYTES:
        logger.error(f"fal_media.generate_seedance_video: download too small ({part.stat().st_size}B) — likely broken")
        part.unlink(missing_ok=True)
        return None
    part.replace(dest)

    logger.info(f"fal_media: seedance ready in {time.time()-t0:.1f}s → {dest}")
    return str(dest)


def generate_kling_video(
    prompt: str,
    dest: "str | Path",
    duration: Duration = 5,
    aspect: Aspect = "9:16",
    negative_prompt: "str | None" = None,
) -> Optional[str]:
    """Generate one cinematic clip via Kling 3.0 Pro (text-to-video).

    Same caller contract as generate_seedance_video (caller passes `dest`), so
    the AI-video engine can swap engines without changing its loop. Kling is the
    stronger model (1080p native, better for fast action) — replaced the weak
    Seedance v1 Pro Fast for B-roll on 2026-06-20 (verified: Seedance rendered
    generic road cars; Kling motorsport fidelity far higher). Returns path str or
    None on any failure (callers must handle None — see module docstring).

    Pricing: flat $0.112/sec at generate_audio=false (audio ON would be $0.168/sec).
    We ALWAYS request generate_audio=false: (1) the montage strips clip audio anyway
    (assembler `-an`) and lays our voiceover+music on top, so native audio is wasted,
    (2) it's 33% cheaper. Resolution-independent (no resolution param → Kling native).
    See ai_video_broll.KLING_PRICE_PER_SEC_USD.
    """
    if not _is_configured():
        logger.warning("fal_media.generate_kling_video: FAL_KEY missing, skipping")
        return None
    if duration not in SUPPORTED_DURATIONS:
        logger.error(f"fal_media.generate_kling_video: bad duration {duration!r} (supported: {SUPPORTED_DURATIONS})")
        return None
    if aspect not in SUPPORTED_ASPECTS:
        logger.error(f"fal_media.generate_kling_video: bad aspect {aspect!r}")
        return None

    try:
        import fal_client
    except ImportError:
        logger.error("fal_media.generate_kling_video: fal-client not installed")
        return None

    dest = Path(dest)
    t0 = time.time()
    logger.info(
        f"fal_media: kling {duration}s {aspect} prompt={prompt[:160]!r} "
        f"neg={(negative_prompt or '')[:80]!r} key={_safe_key_preview()}"
    )

    try:
        result = fal_client.subscribe(
            VIDEO_T2V_ENDPOINT,
            arguments={
                "prompt": prompt,
                "duration": str(duration),
                "aspect_ratio": aspect,
                "generate_audio": False,   # звук монтаж выкидывает + audio off дешевле ($0.112 vs $0.168/с)
                # negative_prompt: жёстко гасит текст/UI/артефакты рук/лиц (схема fal v3/pro
                # принимает поле; дефолт fal — "blur, distort, low quality"). Шлём только если задан.
                **({"negative_prompt": negative_prompt} if negative_prompt else {}),
            },
            with_logs=False,
            start_timeout=SEEDANCE_TIMEOUT_S,
            client_timeout=SEEDANCE_TIMEOUT_S,
        )
    except Exception as e:
        logger.error(f"fal_media.generate_kling_video: API error: {type(e).__name__}: {str(e)[:200]}")
        return None

    video = result.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        logger.error(f"fal_media.generate_kling_video: no video.url in result: {result!r}")
        return None

    # Atomic + validated: download to .part, reject tiny/broken, then rename.
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        _download_timeout(url, part)
    except Exception as e:
        logger.error(f"fal_media.generate_kling_video: download failed: {e}")
        part.unlink(missing_ok=True)
        return None
    if part.stat().st_size < SEEDANCE_MIN_BYTES:
        logger.error(f"fal_media.generate_kling_video: download too small ({part.stat().st_size}B) — likely broken")
        part.unlink(missing_ok=True)
        return None
    part.replace(dest)

    logger.info(f"fal_media: kling ready in {time.time()-t0:.1f}s → {dest}")
    return str(dest)
