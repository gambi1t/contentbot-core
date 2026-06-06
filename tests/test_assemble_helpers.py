"""TDD for assemble_helpers — pure helpers extracted from bot.py card_assemble
post-success screen.

After the auto-montage builds final_auto.mp4 / final_video.mp4, the user lands
on a screen with [📢 Кросс-постинг / 🔄 Пересобрать / ◀️ К карточке]. The
music button was missing — pipeline 3 users (Артём included) would just hit
publish without ever realising they could add music. This module's job:
compute the right button label + callback for the music shortcut.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from assemble_helpers import music_button_label, has_music_mix


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


# ── has_music_mix ────────────────────────────────────────────────────────────

def test_has_music_mix_false_when_file_absent(tmp_path):
    assert has_music_mix(tmp_path) is False


def test_has_music_mix_true_when_file_exists(tmp_path):
    _touch(tmp_path / "final_video_with_music.mp4")
    assert has_music_mix(tmp_path) is True


def test_has_music_mix_handles_none(tmp_path):
    """If the caller passes None (project not resolved) — return False."""
    assert has_music_mix(None) is False


def test_has_music_mix_handles_missing_dir(tmp_path):
    """Path that doesn't exist — return False, no crash."""
    assert has_music_mix(tmp_path / "_missing_") is False


# ── music_button_label ───────────────────────────────────────────────────────

def test_music_button_label_add_when_no_mix(tmp_path):
    label = music_button_label(tmp_path)
    assert "🎵" in label
    assert "Добавить" in label


def test_music_button_label_change_when_mix_present(tmp_path):
    _touch(tmp_path / "final_video_with_music.mp4")
    label = music_button_label(tmp_path)
    assert "🎵" in label
    assert "Сменить" in label


def test_music_button_label_handles_none_proj(tmp_path):
    """If project_dir is None — default to «Добавить» (user has no music yet)."""
    label = music_button_label(None)
    assert "Добавить" in label


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
