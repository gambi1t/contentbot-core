"""
fal.ai media generation module — Nano Banana Pro (image) + Kling 3.0 Pro (video).

Public API:
    generate_image(prompt, aspect="9:16")       -> str | None   (path to PNG)
    generate_video(prompt, duration=5,          -> str | None   (path to MP4)
                   aspect="9:16")
    generate_video_from_image(prompt, image,    -> str | None   (path to MP4)
                              duration=5)

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

SUPPORTED_DURATIONS = (5, 10)
SUPPORTED_ASPECTS = ("9:16", "16:9", "1:1")

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
