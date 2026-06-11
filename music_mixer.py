"""Music mixing module.

Overlays a background music track onto the final video, ducking the music
-18dB whenever voice is present (sidechain compression via ffmpeg).

Tracks live under /root/content-bot/music/<category>/*.mp3
Categories: energetic, chill, cinematic, corporate
"""

import json
import logging
import random
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MUSIC_DIR = Path("/root/content-bot/music")
TRACKS_JSON = MUSIC_DIR / "tracks.json"


def list_categories() -> dict:
    """Return category meta from tracks.json."""
    if not TRACKS_JSON.exists():
        return {}
    with open(TRACKS_JSON) as f:
        data = json.load(f)
    return {cat: info.get("meta", {}) for cat, info in data.items()}


def list_tracks(category: str) -> list:
    """Return list of tracks in a category."""
    if not TRACKS_JSON.exists():
        return []
    with open(TRACKS_JSON) as f:
        data = json.load(f)
    return data.get(category, {}).get("tracks", [])


def pick_random_track(category: str) -> dict | None:
    """Pick a random track from a category."""
    tracks = list_tracks(category)
    return random.choice(tracks) if tracks else None


def mix_music_into_video(
    video_path: str,
    music_path: str,
    output_path: str,
    music_volume_db: float = -18.0,
    duck_db: float = -6.0,
    duck_threshold: float = 0.05,
) -> bool:
    """
    Overlay music onto video with sidechain ducking.

    Args:
        video_path: Input video (with voice)
        music_path: Background music MP3
        output_path: Output MP4 with mixed audio
        music_volume_db: Base music volume in dB (negative = quieter)
        duck_db: Additional reduction when voice is detected
        duck_threshold: Voice detection sensitivity (lower = more sensitive)

    Strategy: simple approach using volume filter — music plays at -18dB
    throughout. Voice stays at original level. Mix them together.
    For ducking, use sidechain compression: when voice peaks above threshold,
    music gets additional -6dB reduction.
    """
    try:
        # Get video duration to trim/loop music appropriately
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10
        )
        video_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0
        if video_duration <= 0:
            logger.error(f"[music_mixer] Could not probe video duration: {video_path}")
            return False

        # ffmpeg filter: music at reduced volume with sidechain ducking from voice
        # [0:a] = video's audio (voice), [1:a] = music
        # Music gets volumed down, then sidechain-ducked by voice
        music_linear = 10 ** (music_volume_db / 20.0)  # convert dB to linear
        filter_complex = (
            # Loop music if needed to cover video duration
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=duration={video_duration}[music_loop];"
            # Set music volume
            f"[music_loop]volume={music_linear}[music_quiet];"
            # Sidechain ducking: music ducked by voice
            f"[music_quiet][0:a]sidechaincompress=threshold={duck_threshold}:ratio=8:attack=20:release=400:makeup=1[music_ducked];"
            # Mix voice + ducked music
            f"[0:a][music_ducked]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]

        logger.info(f"[music_mixer] Mixing music into video: {Path(music_path).name} -> {Path(output_path).name}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            logger.error(f"[music_mixer] ffmpeg failed: {result.stderr[-500:]}")
            # Fallback: simpler mix without sidechain
            return _simple_mix(video_path, music_path, output_path, music_volume_db)

        if not Path(output_path).exists() or Path(output_path).stat().st_size < 1000:
            logger.error(f"[music_mixer] Output file missing or too small")
            return False

        logger.info(f"[music_mixer] Success: {output_path}")
        return True

    except Exception as e:
        logger.error(f"[music_mixer] Exception: {e}")
        return False


def _simple_mix(video_path: str, music_path: str, output_path: str, music_volume_db: float = -20.0) -> bool:
    """Fallback: simple volume-based mix without sidechain (more compatible)."""
    try:
        music_linear = 10 ** (music_volume_db / 20.0)
        filter_complex = (
            f"[1:a]volume={music_linear}[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and Path(output_path).exists():
            logger.info(f"[music_mixer] Simple mix success: {output_path}")
            return True
        logger.error(f"[music_mixer] Simple mix failed: {result.stderr[-500:]}")
        return False
    except Exception as e:
        logger.error(f"[music_mixer] Simple mix exception: {e}")
        return False


if __name__ == "__main__":
    # Quick sanity test
    cats = list_categories()
    print(f"Categories: {list(cats.keys())}")
    for cat, meta in cats.items():
        tracks = list_tracks(cat)
        print(f"  {meta.get('emoji', '')} {cat}: {len(tracks)} tracks")
