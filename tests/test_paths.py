"""TDD for paths.py — single source of truth for filesystem locations.

The bot historically hardcoded /root/maksim-bot/* paths everywhere, which
broke on nox-maksim (user maksim-bot has no access to /root). paths.py
centralises BOT_ROOT (derived from __file__ by default, env-overridable
via MAKSIM_BOT_ROOT) and exposes resolved sub-paths for assets/youtube
cookies/etc.
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_paths(monkeypatch, env_value=None):
    """Re-import paths module with given MAKSIM_BOT_ROOT env (or unset)."""
    if env_value is None:
        monkeypatch.delenv("MAKSIM_BOT_ROOT", raising=False)
    else:
        monkeypatch.setenv("MAKSIM_BOT_ROOT", str(env_value))
    if "paths" in sys.modules:
        del sys.modules["paths"]
    import paths as p
    return p


# ── BOT_ROOT ─────────────────────────────────────────────────────────────────

def test_bot_root_defaults_to_module_dir(monkeypatch):
    p = _fresh_paths(monkeypatch)
    # When env not set, BOT_ROOT must be the directory containing paths.py
    expected = Path(p.__file__).resolve().parent
    assert p.BOT_ROOT == expected


def test_bot_root_env_override(monkeypatch, tmp_path):
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.BOT_ROOT == tmp_path


# ── ASSETS_DIR / YOUTUBE_COOKIES / etc ───────────────────────────────────────

def test_assets_dir_under_bot_root(monkeypatch, tmp_path):
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.ASSETS_DIR == tmp_path / "assets"


def test_youtube_cookies_under_assets(monkeypatch, tmp_path):
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.YOUTUBE_COOKIES == tmp_path / "assets" / "youtube_cookies.txt"


def test_youtube_clips_dir_under_assets(monkeypatch, tmp_path):
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.YOUTUBE_CLIPS_DIR == tmp_path / "assets" / "youtube_clips"


def test_yt_task_file_at_bot_root(monkeypatch, tmp_path):
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.YT_TASK_FILE == tmp_path / "yt_task.json"


def test_library_photos_dir_under_bot_root(monkeypatch, tmp_path):
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.LIBRARY_PHOTOS_DIR == tmp_path / "broll-library" / "photos"


def test_library_photos_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_LIBRARY_PHOTOS_DIR", "/some/other/photos")
    if "paths" in sys.modules:
        del sys.modules["paths"]
    import paths as p
    assert str(p.LIBRARY_PHOTOS_DIR) == str(Path("/some/other/photos"))


def test_library_clips_dir_under_bot_root(monkeypatch, tmp_path):
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.LIBRARY_CLIPS_DIR == tmp_path / "broll-library" / "clips"


def test_library_clips_dir_env_override(monkeypatch):
    monkeypatch.setenv("MAKSIM_LIBRARY_CLIPS_DIR", "/some/other/clips")
    if "paths" in sys.modules:
        del sys.modules["paths"]
    import paths as p
    assert str(p.LIBRARY_CLIPS_DIR) == str(Path("/some/other/clips"))


def test_cover_library_dir_default_points_to_maksim_avatars(monkeypatch, tmp_path):
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.COVER_LIBRARY_DIR == tmp_path / "assets" / "avatars" / "maksim"


def test_cover_library_dir_separate_from_broll_photos(monkeypatch, tmp_path):
    """Cover-библиотека НЕ должна совпадать с B-roll photos (разные источники)."""
    p = _fresh_paths(monkeypatch, tmp_path)
    assert p.COVER_LIBRARY_DIR != p.LIBRARY_PHOTOS_DIR


def test_cover_library_dir_env_override(monkeypatch):
    monkeypatch.setenv("MAKSIM_COVER_LIBRARY_DIR", "/some/covers")
    if "paths" in sys.modules:
        del sys.modules["paths"]
    import paths as p
    assert str(p.COVER_LIBRARY_DIR) == str(Path("/some/covers"))


# ── REMOTE_BOT_ROOT (for yt_helper.py — paths on the bot's server, not local) ─

def test_remote_bot_root_default(monkeypatch):
    monkeypatch.delenv("REMOTE_BOT_ROOT", raising=False)
    p = _fresh_paths(monkeypatch)
    # Sensible default for tenant deploy: /home/maksim-bot/maksim-bot
    assert p.REMOTE_BOT_ROOT == "/home/maksim-bot/maksim-bot"


def test_remote_bot_root_env_override(monkeypatch):
    monkeypatch.setenv("REMOTE_BOT_ROOT", "/opt/some/other/path")
    p = _fresh_paths(monkeypatch)
    assert p.REMOTE_BOT_ROOT == "/opt/some/other/path"


def test_remote_clips_dir_derived_from_remote_root(monkeypatch):
    monkeypatch.setenv("REMOTE_BOT_ROOT", "/opt/x")
    p = _fresh_paths(monkeypatch)
    assert p.REMOTE_YOUTUBE_CLIPS_DIR == "/opt/x/assets/youtube_clips"


def test_remote_yt_task_file_derived(monkeypatch):
    monkeypatch.setenv("REMOTE_BOT_ROOT", "/opt/x")
    p = _fresh_paths(monkeypatch)
    assert p.REMOTE_YT_TASK_FILE == "/opt/x/yt_task.json"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
