"""
YouTube Download Helper — runs on local machine.
Checks server for YouTube download tasks, downloads locally, uploads to server.
"""
import subprocess
import time
import json
import sys
import os
from pathlib import Path

import paths

# Config
SERVER = os.getenv("YT_HELPER_SERVER", "178.104.133.148")
SSH_KEY = os.path.expanduser("~/.ssh/id_rsa")
REMOTE_DIR = paths.REMOTE_YOUTUBE_CLIPS_DIR
LOCAL_DIR = Path(__file__).parent / "assets" / "youtube_clips"
TASK_FILE = paths.REMOTE_YT_TASK_FILE
CHECK_INTERVAL = 5  # seconds

LOCAL_DIR.mkdir(parents=True, exist_ok=True)


def ssh_cmd(cmd: str, timeout: int = 30) -> str:
    """Run command on server via SSH."""
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
         "-i", SSH_KEY, f"root@{SERVER}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip()


def scp_upload(local_path: str, remote_path: str):
    """Upload file to server."""
    subprocess.run(
        ["scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
         "-i", SSH_KEY, local_path, f"root@{SERVER}:{remote_path}"],
        capture_output=True, timeout=120,
    )


def download_youtube(url: str) -> str:
    """Download YouTube video locally using yt-dlp."""
    video_path = str(LOCAL_DIR / "source.mp4")
    # Remove old file
    if os.path.exists(video_path):
        os.unlink(video_path)

    # Use single format (no merge needed) to avoid ffmpeg dependency
    cmd = ["yt-dlp", "--js-runtimes", "node",
         "-f", "best[height<=720][ext=mp4]/best[height<=720]",
         "--max-filesize", "100M",
         "-o", video_path,
         "--no-playlist",
         url]
    log(f"[yt_helper] Running: {' '.join(cmd[:4])}...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    log(f"[yt_helper] yt-dlp exit code: {result.returncode}")
    if result.stderr:
        log(f"[yt_helper] stderr: {result.stderr[:300]}")
    if not os.path.exists(video_path):
        raise RuntimeError(f"Download failed: {result.stderr[:200]}")
    return video_path


def log(msg):
    print(msg, flush=True)


def main():
    log(f"[yt_helper] Watching for YouTube download tasks...")
    log(f"[yt_helper] Server: {SERVER}")
    log(f"[yt_helper] Check interval: {CHECK_INTERVAL}s")
    log(f"[yt_helper] Press Ctrl+C to stop")

    while True:
        try:
            # Check for task on server
            raw = ssh_cmd(f"cat {TASK_FILE} 2>/dev/null")
            if raw:
                task = json.loads(raw)
                url = task.get("url", "")
                status = task.get("status", "")

                if status == "pending" and url:
                    task_id = task.get("id", "")
                    log(f"[yt_helper] New task ({task_id}): {url}")

                    # Mark as downloading (preserve id)
                    ssh_cmd(f'echo \'{{"url":"{url}","status":"downloading","id":"{task_id}"}}\' > {TASK_FILE}')

                    try:
                        # Download locally
                        log(f"[yt_helper] Downloading...")
                        video_path = download_youtube(url)
                        file_size = os.path.getsize(video_path)
                        log(f"[yt_helper] Downloaded: {file_size / 1024 / 1024:.1f} MB")

                        # Upload to server
                        log(f"[yt_helper] Uploading to server...")
                        ssh_cmd(f"mkdir -p {REMOTE_DIR}")
                        scp_upload(video_path, f"{REMOTE_DIR}/source.mp4")
                        log(f"[yt_helper] Uploaded!")

                        # Mark as done (preserve id)
                        ssh_cmd(f'echo \'{{"url":"{url}","status":"done","id":"{task_id}"}}\' > {TASK_FILE}')
                        log(f"[yt_helper] Task complete!")

                    except Exception as e:
                        log(f"[yt_helper] Error: {e}")
                        error_msg = str(e).replace("'", "").replace('"', '')[:200]
                        ssh_cmd(f'echo \'{{"url":"{url}","status":"error","id":"{task_id}","error":"{error_msg}"}}\' > {TASK_FILE}')

        except json.JSONDecodeError:
            pass
        except KeyboardInterrupt:
            print("\n[yt_helper] Stopped.")
            sys.exit(0)
        except Exception as e:
            log(f"[yt_helper] Connection error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
