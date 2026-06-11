"""Тесты music_mixer — pure helpers (без ffmpeg I/O).

Реальный микс/отправка аудио тестируется через Telethon. Здесь — только
helpers выбора треков для превью (поток музыки в готовых карточках).

Запуск: python test_music_mixer.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import music_mixer  # noqa: E402

FAKE_TRACKS = {
    "corporate": [
        {"id": "corp_1", "file": "/m/corp/1.mp3", "duration": 133.0},
        {"id": "corp_2", "file": "/m/corp/2.mp3", "duration": 136.0},
        {"id": "corp_3", "file": "/m/corp/3.mp3", "duration": 97.0},
        {"id": "corp_4", "file": "/m/corp/4.mp3", "duration": 110.0},
    ],
    "solo": [
        {"id": "solo_1", "file": "/m/solo/1.mp3", "duration": 88.0},
    ],
}


def test_pick_n_tracks_returns_n_distinct():
    with patch("music_mixer.list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        tracks = music_mixer.pick_n_tracks("corporate", n=3)
    assert len(tracks) == 3, f"Expected 3, got {len(tracks)}"
    ids = [t["id"] for t in tracks]
    assert len(set(ids)) == 3, f"Дубликаты: {ids}"
    print("  OK pick_n_tracks 3 distinct")


def test_pick_n_tracks_caps_at_available():
    with patch("music_mixer.list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        tracks = music_mixer.pick_n_tracks("solo", n=3)
    assert len(tracks) == 1, f"Expected 1, got {len(tracks)}"
    print("  OK pick_n_tracks caps at available")


def test_pick_n_tracks_empty_category():
    with patch("music_mixer.list_tracks", return_value=[]):
        assert music_mixer.pick_n_tracks("nope", n=3) == []
    print("  OK pick_n_tracks empty → []")


def test_pick_n_tracks_excludes_shown():
    with patch("music_mixer.list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        tracks = music_mixer.pick_n_tracks("corporate", n=3, exclude_ids=["corp_1"])
    ids = [t["id"] for t in tracks]
    assert "corp_1" not in ids, f"Показанный не исключён: {ids}"
    assert len(tracks) == 3, f"Expected 3 из оставшихся, got {len(tracks)}"
    print("  OK pick_n_tracks excludes shown")


def test_pick_n_tracks_fallback_when_all_excluded():
    with patch("music_mixer.list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        tracks = music_mixer.pick_n_tracks(
            "solo", n=2, exclude_ids=["solo_1"],
        )
    assert len(tracks) == 1, f"fallback → 1 (всего 1), got {len(tracks)}"
    print("  OK pick_n_tracks fallback when all excluded")


if __name__ == "__main__":
    tests = [(n, fn) for n, fn in globals().items() if n.startswith("test_") and callable(fn)]
    failed = 0
    print(f"\n{'='*70}\nRunning {len(tests)} tests in test_music_mixer.py\n{'='*70}")
    for name, fn in tests:
        print(f"> {name}")
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"  X FAIL: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{'='*70}\n{'GREEN' if failed == 0 else 'RED'}: {len(tests)-failed}/{len(tests)} passed\n{'='*70}")
    sys.exit(0 if failed == 0 else 1)
