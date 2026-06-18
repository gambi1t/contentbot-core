"""TDD for Phase 2 (fullscreen AI-video in Pipeline 2): clip-count from an
estimated voiceover length (the voiceover is generated AFTER clips, so the count
is estimated from word count ~150 wpm). Approx + 1 buffer clip; the assembler
trims the slight excess. Pure helpers — unit-testable.
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai_video_broll  # noqa: E402


def test_estimate_voiceover_sec_from_words():
    assert ai_video_broll.estimate_voiceover_sec(" ".join(["w"] * 150)) == pytest.approx(60.0)
    assert ai_video_broll.estimate_voiceover_sec(" ".join(["w"] * 90)) == pytest.approx(36.0)
    assert ai_video_broll.estimate_voiceover_sec("") == pytest.approx(0.0)


def test_clips_needed_covers_with_buffer():
    # 36s @ 10s clips → ceil(36/10)=4 + 1 buffer = 5
    assert ai_video_broll.clips_needed(36.0, 10) == 5
    # 30s @ 5s clips → ceil(30/5)=6 + 1 = 7
    assert ai_video_broll.clips_needed(30.0, 5) == 7
    # 40s @ 10s → ceil=4 +1 = 5
    assert ai_video_broll.clips_needed(40.0, 10) == 5


def test_clips_needed_floor_is_min_clips():
    # tiny/empty script must still yield at least MIN_CLIPS
    assert ai_video_broll.clips_needed(2.0, 10) >= ai_video_broll.MIN_CLIPS
    assert ai_video_broll.clips_needed(0.0, 10) >= ai_video_broll.MIN_CLIPS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
