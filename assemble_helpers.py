"""Pure helpers for the card_assemble (Pipeline 3) post-success screen.

After the auto-montage builds final_*.mp4, the user should see a music
shortcut button right there — without having to navigate back to the card
menu and hunt for it. (Pre-fix UX issue: Артём 8 июня сообщил «звук либо
пропустил, либо не наложился» — корневая причина: кнопки музыки не было
на финальном экране сборки.)

Single source of truth:
  - final_video_with_music.mp4 exists → user already added music
  - otherwise → user has no music yet
"""
from __future__ import annotations

from pathlib import Path

_MIXED_FILENAME = "final_video_with_music.mp4"


def has_music_mix(project_dir: Path | None) -> bool:
    """True iff the project already contains a music-mixed final video."""
    if project_dir is None:
        return False
    p = Path(project_dir)
    if not p.exists() or not p.is_dir():
        return False
    return (p / _MIXED_FILENAME).exists()


def music_button_label(project_dir: Path | None) -> str:
    """Choose the right label for the music shortcut on the assemble screen.

    «Сменить музыку» when there's already a mix — clarifies that clicking
    replaces, not duplicates. «Добавить музыку» otherwise.
    """
    return "🎵 Сменить музыку" if has_music_mix(project_dir) else "🎵 Добавить музыку"
