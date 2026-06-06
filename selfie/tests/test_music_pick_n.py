"""TDD for selfie.music.pick_n_tracks — батч-выбор N треков для preview-UX
(3 трека юзеру на прослушивание перед mix-выбором).
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from selfie.music import pick_n_tracks


_FAKE_TRACKS = [
    {"id": "chill_1", "file": "/lib/chill/1.mp3", "duration": 60},
    {"id": "chill_2", "file": "/lib/chill/2.mp3", "duration": 70},
    {"id": "chill_3", "file": "/lib/chill/3.mp3", "duration": 80},
    {"id": "chill_4", "file": "/lib/chill/4.mp3", "duration": 90},
    {"id": "chill_5", "file": "/lib/chill/5.mp3", "duration": 100},
]


def test_pick_n_tracks_default_three(monkeypatch):
    with mock.patch("selfie.music._list_tracks", return_value=_FAKE_TRACKS):
        out = pick_n_tracks("chill")
    assert len(out) == 3
    ids = {t["id"] for t in out}
    assert len(ids) == 3, f"должны быть все разные, получили {ids}"


def test_pick_n_tracks_custom_n(monkeypatch):
    with mock.patch("selfie.music._list_tracks", return_value=_FAKE_TRACKS):
        out = pick_n_tracks("chill", n=5)
    assert len(out) == 5


def test_pick_n_tracks_n_larger_than_available(monkeypatch):
    with mock.patch("selfie.music._list_tracks", return_value=_FAKE_TRACKS[:2]):
        out = pick_n_tracks("chill", n=3)
    assert len(out) == 2


def test_pick_n_tracks_empty_category(monkeypatch):
    with mock.patch("selfie.music._list_tracks", return_value=[]):
        out = pick_n_tracks("chill")
    assert out == []


def test_pick_n_tracks_excludes_given_ids(monkeypatch):
    with mock.patch("selfie.music._list_tracks", return_value=_FAKE_TRACKS):
        out = pick_n_tracks("chill", n=3, exclude_ids=["chill_1", "chill_2"])
    ids = {t["id"] for t in out}
    assert "chill_1" not in ids
    assert "chill_2" not in ids
    assert len(ids) == 3


def test_pick_n_tracks_fallback_when_all_excluded(monkeypatch):
    """Если все треки в exclude — даём из общего пула (не пустоту)."""
    all_ids = [t["id"] for t in _FAKE_TRACKS]
    with mock.patch("selfie.music._list_tracks", return_value=_FAKE_TRACKS):
        out = pick_n_tracks("chill", n=3, exclude_ids=all_ids)
    assert len(out) == 3  # fallback


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
