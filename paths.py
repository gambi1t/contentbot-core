"""Single source of truth for filesystem paths used by the bot.

Historically the bot hardcoded /root/maksim-bot/* in several modules
(crosspost.py, launch_monitor.py, music_mixer.py, yt_helper.py). That
broke on nox-maksim where the bot runs as user maksim-bot with no /root
access. Use the constants from this module instead of literal paths.

Override knobs (both optional):
- MAKSIM_BOT_ROOT — local bot installation root. Default: directory of
  this file. Set on the bot host if the repo lives somewhere unusual.
- REMOTE_BOT_ROOT — remote bot root used by yt_helper.py for ssh/scp
  commands to the bot's server. Default: /home/maksim-bot/maksim-bot.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Local (this host) ───────────────────────────────────────────────────────

# Resolved once at import. Derived from __file__ so it works correctly in any
# deployment without needing env config; env override exists as escape hatch.
BOT_ROOT: Path = Path(
    os.getenv("MAKSIM_BOT_ROOT", str(Path(__file__).resolve().parent))
)

ASSETS_DIR: Path = BOT_ROOT / "assets"
YOUTUBE_COOKIES: Path = ASSETS_DIR / "youtube_cookies.txt"
YOUTUBE_CLIPS_DIR: Path = ASSETS_DIR / "youtube_clips"
YT_TASK_FILE: Path = BOT_ROOT / "yt_task.json"

# Cover library for selfie pipeline: photos to use as video cover.
# Override with MAKSIM_LIBRARY_PHOTOS_DIR if hosted elsewhere on the bot's host.
LIBRARY_PHOTOS_DIR: Path = Path(
    os.getenv("MAKSIM_LIBRARY_PHOTOS_DIR", str(BOT_ROOT / "broll-library" / "photos"))
)

# B-roll video clips for Pipeline 2 (selfie + B-roll mix).
# Same broll-library/clips tree used by the auto-broll pipeline.
LIBRARY_CLIPS_DIR: Path = Path(
    os.getenv("MAKSIM_LIBRARY_CLIPS_DIR", str(BOT_ROOT / "broll-library" / "clips"))
)

# Cover library for the selfie COVER picker — это ГОТОВЫЕ ПОРТРЕТЫ/АВАТАРЫ
# владельца бренда (Максима), а НЕ B-roll-кадры (glamping/karting).
# «Из библиотеки» при выборе обложки = эти фото. Отдельно от LIBRARY_PHOTOS_DIR
# (которая = B-roll footage для вставок в ролик).
COVER_LIBRARY_DIR: Path = Path(
    os.getenv("MAKSIM_COVER_LIBRARY_DIR", str(BOT_ROOT / "assets" / "avatars" / "maksim"))
)


# ── Remote (paths inside the bot's host, used over SSH from elsewhere) ──────

# yt_helper.py is a standalone script that runs from the operator's
# workstation, ssh's into the bot's server, and reads/writes files there.
# These strings are *server* paths, not local — kept separate from BOT_ROOT.

REMOTE_BOT_ROOT: str = os.getenv("REMOTE_BOT_ROOT", "/home/maksim-bot/maksim-bot")
REMOTE_YOUTUBE_CLIPS_DIR: str = f"{REMOTE_BOT_ROOT}/assets/youtube_clips"
REMOTE_YT_TASK_FILE: str = f"{REMOTE_BOT_ROOT}/yt_task.json"
