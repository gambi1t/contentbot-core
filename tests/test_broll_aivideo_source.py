"""TDD for Phase 2 узел B — AI-video (Seedance) as a Pipeline-2 source.

Pure parts unit-tested: the fullscreen clip-plan (count+cost for the confirm
screen), the menu button, and parse_source_cb accepting the confirm/back
pseudo-modes (so the b2src router reaches them WITHOUT touching bot.py). The
async handler dispatch is Telethon-verified.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_fullscreen_plan_count_and_cost():
    import ai_video_broll as av
    p = av.fullscreen_plan(" ".join(["w"] * 90), clip_len=10)   # 90 words ≈ 36s
    assert p["n_clips"] == 4                                    # ceil(36/10), без +1 буфера (kling-switch 20.06)
    assert p["clip_len"] == 10
    assert p["est_sec"] == pytest.approx(36.0)
    assert p["cost"] == pytest.approx(4 * 10 * av.KLING_PRICE_PER_SEC_USD)   # 4 клипа × 10с × Kling $0.112/с


pytest.importorskip("telegram")


def test_source_menu_shows_aivideo_when_enabled():
    from broll.source_menu import source_menu_keyboard
    from broll.draft import SourceMode
    kb = source_menu_keyboard("d1", enabled_modes=[SourceMode.AI_VIDEO])
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "b2src:ai_video:d1" in cbs


def test_parse_accepts_aivideo_pseudomodes():
    from broll.source_menu import parse_source_cb
    assert parse_source_cb("b2src:ai_video:d1") == ("ai_video", "d1")
    assert parse_source_cb("b2src:ai_video_go:d1") == ("ai_video_go", "d1")
    assert parse_source_cb("b2src:ai_video_menu:d1") == ("ai_video_menu", "d1")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
