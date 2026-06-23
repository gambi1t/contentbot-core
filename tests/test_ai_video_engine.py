"""TDD for ai_video_broll.generate_ai_broll — the engine (Phase 1, узел 3).

Wires the director (узел 2) to the Seedance primitive (узел 1) and returns the
same contract as the other engines: (list[Path], cost_usd). Director and
Seedance are monkeypatched so this isolates the engine's own logic: namespacing
under aivideo/, per-clip failure tolerance, cost estimate, progress callback.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _patch_director(monkeypatch, n):
    import ai_video_broll
    monkeypatch.setattr(
        ai_video_broll, "plan_clips",
        lambda script, claude, max_clips=ai_video_broll.MAX_CLIPS, target_clips=None,
            business_context=None:
            [{"beat": f"b{i}", "prompt": f"p{i}"} for i in range(n)],
    )


def _patch_kling(monkeypatch, fail_calls=()):
    """Stub generate_kling_video: writes a dummy file unless its call index is in fail_calls.

    Also stubs the preflight readiness check to True (no real FAL_KEY in tests).
    """
    import ai_video_broll
    monkeypatch.setattr(ai_video_broll.fal_media, "kling_ready", lambda: (True, ""))
    state = {"n": 0}

    def gen(prompt, dest, duration=5, aspect="9:16", negative_prompt=None, errors_out=None):
        state["n"] += 1
        if state["n"] in fail_calls:
            return None
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"x")
        return str(dest)

    monkeypatch.setattr(ai_video_broll.fal_media, "generate_kling_video", gen)


# ── contract: clips + namespace + cost ───────────────────────────────────────

def test_engine_returns_paths_and_cost(monkeypatch, tmp_path):
    import ai_video_broll
    _patch_director(monkeypatch, 3)
    _patch_kling(monkeypatch)
    paths, cost = ai_video_broll.generate_ai_broll("script", tmp_path, claude=object(), duration=5)
    assert len(paths) == 3
    assert all(p.exists() for p in paths)
    assert isinstance(cost, float) and cost > 0


def test_engine_clips_named_under_aivideo(monkeypatch, tmp_path):
    import ai_video_broll
    _patch_director(monkeypatch, 2)
    _patch_kling(monkeypatch)
    paths, _ = ai_video_broll.generate_ai_broll("script", tmp_path, claude=object())
    assert paths[0] == tmp_path / "aivideo" / "ai_01.mp4"
    assert paths[1] == tmp_path / "aivideo" / "ai_02.mp4"


def test_engine_cost_scales_with_count_and_duration(monkeypatch, tmp_path):
    import ai_video_broll
    _patch_director(monkeypatch, 3)
    _patch_kling(monkeypatch)
    _, cost = ai_video_broll.generate_ai_broll("s", tmp_path, claude=object(), duration=10)
    # 3 clips * 10s * $0.112/sec (Kling) = 3.36
    assert cost == pytest.approx(3 * 10 * ai_video_broll.KLING_PRICE_PER_SEC_USD)


# ── resilience ────────────────────────────────────────────────────────────────

def test_engine_partial_when_some_clips_fail(monkeypatch, tmp_path):
    import ai_video_broll
    _patch_director(monkeypatch, 3)
    _patch_kling(monkeypatch, fail_calls=(2,))   # 2nd clip fails
    paths, cost = ai_video_broll.generate_ai_broll("s", tmp_path, claude=object(), duration=5)
    assert len(paths) == 2                          # the two that succeeded
    # cost only for delivered clips: 2 * 5s * $0.112/sec (Kling)
    assert cost == pytest.approx(2 * 5 * ai_video_broll.KLING_PRICE_PER_SEC_USD)


def test_engine_raises_when_all_clips_fail(monkeypatch, tmp_path):
    import ai_video_broll
    _patch_director(monkeypatch, 3)
    _patch_kling(monkeypatch, fail_calls=(1, 2, 3))
    with pytest.raises(ai_video_broll.AiVideoError):
        ai_video_broll.generate_ai_broll("s", tmp_path, claude=object())


# ── progress callback (fire-and-forget, sturdy) ──────────────────────────────

def test_engine_calls_progress_cb(monkeypatch, tmp_path):
    import ai_video_broll
    _patch_director(monkeypatch, 2)
    _patch_kling(monkeypatch)
    seen = []
    ai_video_broll.generate_ai_broll("s", tmp_path, claude=object(), progress_cb=seen.append)
    assert len(seen) >= 1


def test_engine_survives_progress_cb_error(monkeypatch, tmp_path):
    import ai_video_broll
    _patch_director(monkeypatch, 2)
    _patch_kling(monkeypatch)

    def boom(_msg):
        raise RuntimeError("cb broke")

    paths, _ = ai_video_broll.generate_ai_broll("s", tmp_path, claude=object(), progress_cb=boom)
    assert len(paths) == 2   # cb failure must not break generation


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
