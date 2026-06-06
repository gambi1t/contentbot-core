"""TDD for env-driven MUSIC_DIR in music_mixer.

The hardcoded path /root/maksim-bot/music broke library lookup on
nox-maksim where the bot runs as user maksim-bot (no root access).
Tests verify env override works and falls back to /srv default.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import music_mixer


def test_get_music_dir_default(monkeypatch):
    monkeypatch.delenv("MAKSIM_MUSIC_DIR", raising=False)
    assert music_mixer._get_music_dir() == Path("/srv/bot-music-maksim")


def test_get_music_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_MUSIC_DIR", str(tmp_path))
    assert music_mixer._get_music_dir() == tmp_path


def test_get_tracks_json_path_uses_music_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_MUSIC_DIR", str(tmp_path))
    assert music_mixer._get_tracks_json() == tmp_path / "tracks.json"


def test_list_categories_empty_when_no_tracks_json(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_MUSIC_DIR", str(tmp_path))
    assert music_mixer.list_categories() == {}


def test_list_categories_returns_meta(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_MUSIC_DIR", str(tmp_path))
    (tmp_path / "tracks.json").write_text(json.dumps({
        "chill": {
            "meta": {"emoji": "😌", "label": "Спокойный", "desc": "Фон"},
            "tracks": [{"id": "chill_1", "file": "chill/chill_1.mp3", "duration": 60.0}],
        },
        "energetic": {
            "meta": {"emoji": "⚡", "label": "Энергично"},
            "tracks": [],
        },
    }), encoding="utf-8")

    cats = music_mixer.list_categories()
    assert "chill" in cats and "energetic" in cats
    assert cats["chill"]["label"] == "Спокойный"


def test_list_tracks_returns_for_existing_category(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_MUSIC_DIR", str(tmp_path))
    (tmp_path / "tracks.json").write_text(json.dumps({
        "chill": {
            "meta": {"label": "Спокойный"},
            "tracks": [
                {"id": "chill_1", "file": "chill/chill_1.mp3"},
                {"id": "chill_2", "file": "chill/chill_2.mp3"},
            ],
        }
    }), encoding="utf-8")

    tracks = music_mixer.list_tracks("chill")
    assert len(tracks) == 2
    assert tracks[0]["id"] == "chill_1"


def test_list_tracks_empty_for_unknown_category(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_MUSIC_DIR", str(tmp_path))
    (tmp_path / "tracks.json").write_text(json.dumps({
        "chill": {"meta": {}, "tracks": []}
    }), encoding="utf-8")

    assert music_mixer.list_tracks("nonexistent") == []


def test_pick_random_track_returns_one(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_MUSIC_DIR", str(tmp_path))
    (tmp_path / "tracks.json").write_text(json.dumps({
        "chill": {
            "meta": {},
            "tracks": [
                {"id": "chill_1", "file": "chill/chill_1.mp3"},
                {"id": "chill_2", "file": "chill/chill_2.mp3"},
            ],
        }
    }), encoding="utf-8")

    picked = music_mixer.pick_random_track("chill")
    assert picked is not None
    assert picked["id"] in {"chill_1", "chill_2"}


def test_pick_random_track_none_when_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("MAKSIM_MUSIC_DIR", str(tmp_path))
    (tmp_path / "tracks.json").write_text(json.dumps({
        "chill": {"meta": {}, "tracks": []}
    }), encoding="utf-8")

    assert music_mixer.pick_random_track("chill") is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
