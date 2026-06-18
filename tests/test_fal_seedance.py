"""TDD for fal_media.generate_seedance_video — ByteDance Seedance Pro Fast t2v.

Phase 1 of "AI-видео по сценарию": the cinematic-clip engine calls Seedance
(not Kling) to render short 9:16 1080p clips from text prompts, and must
control the output path — clips land in a per-project namespace the engine
picks (e.g. proj/aivideo/ai_NN.mp4). This primitive mirrors the existing
generate_video (Kling) call; the only justified additions are an explicit
`dest` path + the `resolution` argument (Seedance is token-priced by
resolution, fal schema requires it). fal_client is mocked — no network.

Verified facts this test pins (fal.ai raw schema, 2026-06-17):
  - t2v endpoint: fal-ai/bytedance/seedance/v1/pro/fast/text-to-video
  - duration is a string enum; we offer the 5/10 subset (model supports 2-12)
  - resolution 1080p, aspect_ratio 9:16 are valid
  - response shape assumed identical to Kling: result["video"]["url"]
    (standard fal video shape; live smoke on server confirms before deploy)
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fake_fal_client(captured, video_url="https://fal.example/out.mp4"):
    """Minimal fal_client stub that records the subscribe() call."""
    mod = types.ModuleType("fal_client")

    def subscribe(endpoint, arguments=None, with_logs=False, **kw):
        captured["endpoint"] = endpoint
        captured["arguments"] = arguments
        return {"video": {"url": video_url}}

    mod.subscribe = subscribe
    return mod


def _stub_download(monkeypatch):
    """Replace network download with a local writer (>= SEEDANCE_MIN_BYTES so the
    size-validation accepts it)."""
    import fal_media

    def fake_dl(url, dest):
        Path(dest).write_bytes(b"x" * 100_000)

    monkeypatch.setattr(fal_media, "_download_timeout", fake_dl)


# ── endpoint + arguments contract ────────────────────────────────────────────

def test_seedance_calls_pro_fast_t2v_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    captured = {}
    monkeypatch.setitem(sys.modules, "fal_client", _fake_fal_client(captured))
    import fal_media
    _stub_download(monkeypatch)

    dest = tmp_path / "ai_01.mp4"
    out = fal_media.generate_seedance_video("a cinematic shot", dest, duration=5)

    assert out == str(dest)
    assert captured["endpoint"] == "fal-ai/bytedance/seedance/v1/pro/fast/text-to-video"


def test_seedance_arguments_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    captured = {}
    monkeypatch.setitem(sys.modules, "fal_client", _fake_fal_client(captured))
    import fal_media
    _stub_download(monkeypatch)

    fal_media.generate_seedance_video("p", tmp_path / "ai_01.mp4", duration=10, aspect="9:16")
    args = captured["arguments"]

    assert args["prompt"] == "p"
    assert args["duration"] == "10"        # str, like Kling
    assert args["aspect_ratio"] == "9:16"
    assert args["resolution"] == "1080p"   # required by Seedance fal schema


# ── output path control (engine owns the namespace) ──────────────────────────

def test_seedance_writes_to_given_dest_creating_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    monkeypatch.setitem(sys.modules, "fal_client", _fake_fal_client({}))
    import fal_media
    _stub_download(monkeypatch)

    dest = tmp_path / "aivideo" / "ai_03.mp4"   # parent dir does not exist yet
    out = fal_media.generate_seedance_video("p", dest)

    assert Path(out) == dest
    assert dest.exists()


# ── graceful failure (callers rely on None, never crash) ─────────────────────

def test_seedance_bad_duration_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    monkeypatch.setitem(sys.modules, "fal_client", _fake_fal_client({}))
    import fal_media
    assert fal_media.generate_seedance_video("p", tmp_path / "x.mp4", duration=7) is None


def test_seedance_missing_key_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("FAL_KEY", raising=False)
    import fal_media
    assert fal_media.generate_seedance_video("p", tmp_path / "x.mp4") is None


def test_seedance_no_video_url_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    mod = types.ModuleType("fal_client")
    mod.subscribe = lambda *a, **k: {"unexpected": True}
    monkeypatch.setitem(sys.modules, "fal_client", mod)
    import fal_media
    assert fal_media.generate_seedance_video("p", tmp_path / "x.mp4") is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
