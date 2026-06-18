"""TDD for the selfie B-roll picker exposing the new AI-video source (узел 4).

Only the pure keyboard helper is unit-tested here; the full async callback flow
(selfie_broll:aivideo -> 5/10 pick -> selfie_broll:aivid:N -> generation) is
verified end-to-end via Telethon, per the selfie test convention. Skips cleanly
if python-telegram-bot isn't importable in this environment.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("telegram")
from selfie.broll_picker import build_picker_keyboard  # noqa: E402


def _callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_picker_offers_aivideo_source():
    cbs = _callbacks(build_picker_keyboard([]))
    assert "selfie_broll:aivideo" in cbs


def test_aivideo_button_hidden_at_limit():
    from selfie.broll_picker import BrollItem, MAX_BROLL_ITEMS
    full = [BrollItem(kind="video", source=Path(f"x{i}.mp4")) for i in range(MAX_BROLL_ITEMS)]
    cbs = _callbacks(build_picker_keyboard(full))
    assert "selfie_broll:aivideo" not in cbs   # add-buttons hide at limit, like the others


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
