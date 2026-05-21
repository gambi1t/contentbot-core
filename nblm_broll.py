"""NotebookLM → B-roll extractor.

Generates a NotebookLM explainer video from a script, then cuts it into
individual B-roll clips using ffmpeg scene detection. Clips get saved as
broll_nblm_NN.mp4 and can be plugged into the existing bot B-roll flow.

Standalone usage (local Windows, for testing):
    python nblm_broll.py \
        --script path/to/script.txt \
        --title "Подписки Apple в России" \
        --out ./nblm_output/

Programmatic usage (from bot.py or other scripts):
    from nblm_broll import generate_and_extract
    clips = generate_and_extract(script_text, title, out_dir)

Requirements:
    - notebooklm CLI installed and authenticated (see reference memory)
    - ffmpeg + ffprobe in PATH
    - On Windows prepend PYTHONIOENCODING=utf-8 to shell if Cyrillic breaks

Notes:
    - NotebookLM ignores length hints — it generates 1-3 min regardless.
      We use the full output as a B-roll source pool.
    - Last ~5 seconds of every NBLM video is an outro with google.com link,
      we automatically skip it.
    - Watermark stays in the lower-right corner of each clip. That's fine
      when the clip is placed in the top half of the split-screen — it's
      small and looks like a source attribution.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("nblm_broll")


class NblmError(Exception):
    """Raised when NBLM CLI or ffmpeg step fails."""


def _run_cli(
    args: list[str],
    *,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a notebooklm CLI command with utf-8 env."""
    cmd = ["notebooklm"] + args
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    logger.info(f"CLI: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    if check and result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-800:]
        raise NblmError(
            f"notebooklm {' '.join(args[:2])} failed (exit {result.returncode}):\n{tail}"
        )
    return result


def _run_ffmpeg(cmd: list[str], desc: str) -> subprocess.CompletedProcess:
    logger.info(f"ffmpeg: {desc}")
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        tail = (result.stderr or "")[-800:]
        raise NblmError(f"ffmpeg {desc} failed:\n{tail}")
    return result


def create_notebook(title: str) -> str:
    """Create a new notebook, return its ID."""
    safe_title = re.sub(r'[^\w\s\-а-яА-ЯёЁ]', '', title)[:80].strip() or "B-roll"
    result = _run_cli(["create", f"[B-roll] {safe_title}", "--json"], timeout=60)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise NblmError(f"create returned non-JSON:\n{result.stdout[:400]}")
    nb = data.get("notebook") if isinstance(data.get("notebook"), dict) else data
    notebook_id = nb.get("id") or nb.get("notebook_id") or nb.get("notebookId")
    if not notebook_id:
        raise NblmError(f"create response has no id field: {data}")
    logger.info(f"Created notebook: {notebook_id}")
    return notebook_id


def add_script_source(notebook_id: str, script_text: str, title: str) -> None:
    """Add the script as an inline text source."""
    # Keep under CLI arg length limit. If script is too long, write to tempfile.
    if len(script_text) > 8000:
        raise NblmError(
            f"Script too long ({len(script_text)} chars). "
            f"NBLM inline text source limit ~8000."
        )
    _run_cli(
        [
            "source", "add", script_text,
            "-n", notebook_id,
            "--type", "text",
            "--title", f"Сценарий: {title}"[:100],
        ],
        timeout=120,
    )
    logger.info("Added script as text source")


def generate_video(
    notebook_id: str,
    title: str,
    style: str = "whiteboard",
    language: str = "ru",
    timeout_sec: int = 1500,
) -> None:
    """Kick off video generation (--no-wait) and poll via `artifact wait`.

    The CLI's built-in --wait has a hard 600s timeout which is too short for
    explainer videos. We use --no-wait + `artifact wait --timeout N` instead.
    """
    prompt = (
        f"Короткий динамичный explainer по теме «{title}». "
        f"Аудитория — предприниматели 30+, русскоязычные. "
        f"Акцент на визуальных метафорах, конкретных иконках приложений, "
        f"чёткой структуре по шагам. Без воды."
    )
    logger.info(f"Starting video generation (style={style}, lang={language})...")
    result = _run_cli(
        [
            "generate", "video",
            "-n", notebook_id,
            "--format", "explainer",
            "--style", style,
            "--language", language,
            "--no-wait",
            "--retry", "2",
            "--json",
            prompt,
        ],
        timeout=120,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise NblmError(f"generate video returned non-JSON:\n{result.stdout[:400]}")
    task_id = (
        data.get("task_id")
        or data.get("id")
        or (data.get("task") or {}).get("id")
        or (data.get("artifact") or {}).get("id")
    )
    if not task_id:
        raise NblmError(f"generate video response has no task id: {data}")
    logger.info(f"Video task started: {task_id}, waiting up to {timeout_sec}s...")

    _run_cli(
        [
            "artifact", "wait", task_id,
            "-n", notebook_id,
            "--timeout", str(timeout_sec),
            "--interval", "15",
        ],
        timeout=timeout_sec + 60,
    )
    logger.info("Video generation done")


def download_video(notebook_id: str, out_path: Path) -> Path:
    """Download the latest generated video to out_path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_cli(
        [
            "download", "video",
            "-n", notebook_id,
            "--latest",
            "--force",
            str(out_path),
        ],
        timeout=300,
    )
    if not out_path.exists() or out_path.stat().st_size < 10_000:
        raise NblmError(f"download video produced missing/tiny file: {out_path}")
    logger.info(f"Downloaded: {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return out_path


def _probe_duration(video_path: Path) -> float:
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
        raise NblmError(f"ffprobe failed: {result.stderr[-400:]}")
    return float(json.loads(result.stdout)["format"]["duration"])


def _detect_scene_cuts(video_path: Path, threshold: float = 0.3) -> list[float]:
    """Return sorted list of scene-change timestamps in seconds."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(video_path),
            "-filter:v", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, errors="replace",
    )
    # showinfo writes to stderr
    timestamps = []
    for line in (result.stderr or "").splitlines():
        m = re.search(r'pts_time:([\d.]+)', line)
        if m:
            timestamps.append(float(m.group(1)))
    return sorted(set(timestamps))


def extract_broll_clips(
    video_path: Path,
    out_dir: Path,
    *,
    skip_head_sec: float = 0.0,
    skip_tail_sec: float = 5.0,
    min_clip_sec: float = 2.0,
    max_clip_sec: float = 10.0,
    scene_threshold: float = 0.3,
    filename_prefix: str = "broll_nblm",
) -> list[Path]:
    """Cut an NBLM video into B-roll segments using scene detection.

    Returns list of saved clip paths.
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    duration = _probe_duration(video_path)
    effective_start = max(0.0, skip_head_sec)
    effective_end = max(effective_start, duration - skip_tail_sec)
    logger.info(
        f"Duration {duration:.1f}с, using [{effective_start:.1f}, {effective_end:.1f}], "
        f"threshold={scene_threshold}"
    )

    cuts = _detect_scene_cuts(video_path, scene_threshold)
    # Build segment boundaries: [start, cut1, cut2, ..., end]
    boundaries = [effective_start]
    for t in cuts:
        if effective_start < t < effective_end:
            boundaries.append(t)
    boundaries.append(effective_end)
    boundaries = sorted(set(boundaries))

    logger.info(f"Scene cuts: {len(cuts)} total, {len(boundaries) - 1} candidate segments")

    clips: list[Path] = []
    idx = 0
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        dur = end - start
        if dur < min_clip_sec:
            logger.debug(f"  skip segment {i}: too short ({dur:.1f}с)")
            continue
        if dur > max_clip_sec:
            # Truncate long segments to max_clip_sec (probably a static scene)
            end = start + max_clip_sec
            dur = max_clip_sec
            logger.debug(f"  segment {i}: truncated to {max_clip_sec}с")
        idx += 1
        out_path = out_dir / f"{filename_prefix}_{idx:02d}.mp4"
        _run_ffmpeg(
            [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-i", str(video_path),
                "-t", f"{dur:.3f}",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-an",  # drop audio — we use avatar's voice track
                str(out_path),
            ],
            f"extract clip {idx} [{start:.1f}-{end:.1f}]",
        )
        clips.append(out_path)

    logger.info(f"✅ Extracted {len(clips)} B-roll clips to {out_dir}")
    return clips


def generate_and_extract(
    script_text: str,
    title: str,
    out_dir: Path,
    *,
    style: str = "whiteboard",
    language: str = "ru",
    keep_source_video: bool = True,
    reuse_notebook_id: str | None = None,
) -> dict:
    """End-to-end: create notebook → add script → generate video → download → extract clips.

    Returns dict with keys: notebook_id, video_path, clips (list[Path])
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"=== NBLM B-roll pipeline: {title} ===")
    t0 = time.time()

    if reuse_notebook_id:
        notebook_id = reuse_notebook_id
        logger.info(f"Reusing notebook: {notebook_id}")
    else:
        notebook_id = create_notebook(title)
        add_script_source(notebook_id, script_text, title)
    generate_video(notebook_id, title, style=style, language=language)

    video_path = out_dir / f"nblm_source_{notebook_id[:8]}.mp4"
    download_video(notebook_id, video_path)

    clips = extract_broll_clips(video_path, out_dir)

    if not keep_source_video:
        try:
            video_path.unlink()
        except Exception:
            pass

    elapsed = time.time() - t0
    logger.info(f"=== Done in {elapsed/60:.1f} min. {len(clips)} clips ready. ===")

    return {
        "notebook_id": notebook_id,
        "video_path": video_path if keep_source_video else None,
        "clips": clips,
        "elapsed_sec": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate B-roll clips from an NBLM explainer video")
    parser.add_argument("--script", type=Path, default=None, help="Path to script text file (UTF-8)")
    parser.add_argument("--title", type=str, default="broll", help="Title for the notebook / project")
    parser.add_argument("--out", type=Path, default=Path("./nblm_output"), help="Output directory")
    parser.add_argument("--style", default="whiteboard",
                        choices=["auto", "classic", "whiteboard", "kawaii", "anime",
                                 "watercolor", "retro-print", "heritage", "paper-craft"])
    parser.add_argument("--language", default="ru")
    parser.add_argument("--skip-tail", type=float, default=5.0, help="Seconds to skip from the end (outro)")
    parser.add_argument("--min-clip", type=float, default=2.0)
    parser.add_argument("--max-clip", type=float, default=10.0)
    parser.add_argument("--threshold", type=float, default=0.3, help="Scene detection threshold (0.2-0.5)")
    parser.add_argument("--extract-only", type=Path, default=None,
                        help="Skip generation, just extract from this existing video")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    safe_folder = re.sub(r'[<>:"/\\|?*]', '_', args.title)[:60].strip()
    out_dir = args.out / safe_folder
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.extract_only:
        print(f"Extract-only mode: {args.extract_only}")
        clips = extract_broll_clips(
            args.extract_only, out_dir,
            skip_tail_sec=args.skip_tail,
            min_clip_sec=args.min_clip,
            max_clip_sec=args.max_clip,
            scene_threshold=args.threshold,
        )
    else:
        script_text = args.script.read_text(encoding="utf-8")
        print(f"Script: {len(script_text)} chars")
        print(f"Title: {args.title}")
        print(f"Style: {args.style}")
        print(f"Output: {out_dir}")
        print()
        print("Step 1/3: Creating NBLM notebook + uploading script...")
        print("Step 2/3: Generating video (5-15 min, grab coffee)...")
        result = generate_and_extract(
            script_text, args.title, out_dir,
            style=args.style, language=args.language,
        )
        clips = result["clips"]
        print()
        print(f"Source video: {result['video_path']}")
        print(f"Took: {result['elapsed_sec']/60:.1f} min")

    print()
    print(f"=== {len(clips)} B-roll clips ready ===")
    for c in clips:
        size_kb = c.stat().st_size / 1024
        print(f"  {c.name} ({size_kb:.0f} KB)")
    print()
    print(f"📁 Full path: {out_dir.resolve()}")
    return 0 if clips else 1


if __name__ == "__main__":
    sys.exit(main())
