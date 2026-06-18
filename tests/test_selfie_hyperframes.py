"""TDD for HyperFrames as a B-roll source in selfie (the gap: selfie had only
Remotion graphics). Pure parts unit-tested; the async handler flow is Telethon-
verified, mirroring the proven 'aivid' skeleton (busy-guard, own job key).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("telegram")
from selfie.broll_picker import build_picker_keyboard  # noqa: E402
from selfie import handlers  # noqa: E402


def test_picker_offers_hyperframes_source():
    cbs = [b.callback_data for row in build_picker_keyboard([]).inline_keyboard for b in row]
    assert "selfie_broll:hf" in cbs


def test_busy_guard_parameterised_by_hf_key():
    data = {"selfie_hf_job_id": {"id": "h1", "ts": 1000.0}}
    assert handlers._aivid_inflight(data, now=1100.0, key="selfie_hf_job_id") is True
    assert handlers._aivid_job_matches(data, "h1", key="selfie_hf_job_id") is True
    stale_now = 1000.0 + handlers.HF_STALE_SEC + 10
    assert handlers._aivid_inflight(data, now=stale_now, key="selfie_hf_job_id") is False
    # independence: an HF job must NOT trip the default (aivid) guard
    assert handlers._aivid_inflight(data, now=1100.0) is False


def test_hf_stale_window_larger_than_aivid():
    # HyperFrames runs 10-25 min → its stale window must exceed Seedance's
    assert handlers.HF_STALE_SEC > handlers.AIVID_STALE_SEC


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
