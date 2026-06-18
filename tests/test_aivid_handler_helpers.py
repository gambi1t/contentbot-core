"""TDD for the pure helpers behind the AI-video selfie-adapter hardening.

The async callback flow is Telethon-verified, but its risky LOGIC is extracted
into pure functions that ARE unit-testable:
  - ai_video_broll.estimate_cost_range — $ estimate shown on the 5/10 screen
  - selfie.handlers._aivid_inflight — busy-guard with staleness (no permanent
    lockout after a restart leaves a stale key in pending)
  - selfie.handlers._aivid_job_matches — post-generation ownership check
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── cost estimate (engine owns the price) ────────────────────────────────────

def test_estimate_cost_range_5s():
    import ai_video_broll
    lo, hi = ai_video_broll.estimate_cost_range(5)
    assert lo == pytest.approx(2 * 0.11)    # MIN_CLIPS @ 720p price
    assert hi == pytest.approx(4 * 0.11)    # MAX_CLIPS


def test_estimate_cost_range_10s():
    import ai_video_broll
    lo, hi = ai_video_broll.estimate_cost_range(10)
    assert lo == pytest.approx(2 * 2 * 0.11)
    assert hi == pytest.approx(4 * 2 * 0.11)


# ── busy-guard with staleness (prevents double-paid start + restart lockout) ──

pytest.importorskip("telegram")   # selfie.handlers imports python-telegram-bot


def test_aivid_inflight_false_when_absent():
    from selfie import handlers
    assert handlers._aivid_inflight({}, now=1000.0) is False


def test_aivid_inflight_true_when_fresh():
    from selfie import handlers
    data = {"selfie_aivid_job_id": {"id": "j1", "ts": 1000.0}}
    assert handlers._aivid_inflight(data, now=1100.0) is True   # 100s < stale window


def test_aivid_inflight_false_when_stale():
    from selfie import handlers
    data = {"selfie_aivid_job_id": {"id": "j1", "ts": 0.0}}
    # older than the stale window → not a live job → allow a fresh start (no lockout)
    assert handlers._aivid_inflight(data, now=handlers.AIVID_STALE_SEC + 10.0) is False


def test_aivid_inflight_false_when_malformed():
    from selfie import handlers
    assert handlers._aivid_inflight({"selfie_aivid_job_id": "legacy_str"}, now=1000.0) is False
    assert handlers._aivid_inflight({"selfie_aivid_job_id": {"id": "j"}}, now=1000.0) is False  # no ts


def test_aivid_job_matches():
    from selfie import handlers
    data = {"selfie_aivid_job_id": {"id": "abc", "ts": 1.0}}
    assert handlers._aivid_job_matches(data, "abc") is True
    assert handlers._aivid_job_matches(data, "xyz") is False
    assert handlers._aivid_job_matches({}, "abc") is False


# ── result message: clean, no per-file list (Артём 18 июня) ──────────────────

def test_aivid_done_text_all_added_is_clean():
    from selfie import handlers
    t = handlers._aivid_done_text(4, 4)
    assert "создано 4" in t
    assert "ai_0" not in t and "[AI-видео]" not in t   # no useless filenames
    assert "Сгенерировано" not in t                     # no paid-note when all fit


def test_aivid_done_text_partial_is_honest():
    from selfie import handlers
    t = handlers._aivid_done_text(2, 4)
    assert "создано 2" in t
    assert "4" in t and "оплачено" in t                 # honest about paid-but-unused


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
