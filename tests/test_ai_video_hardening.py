"""TDD for the hardening pass on the AI-video engine (post adversarial review).

Covers the engine-core fixes (fal_media + ai_video_broll), all unit-testable:
  - Seedance: subscribe timeouts, atomic .part + size validation, preflight readiness
  - director: max_clips cap, blank/short-prompt drop, repair-feedback on retry,
    retry on transient create() exception
  - engine: preflight before the Claude director, max_clips threaded to director
    and capping paid Seedance calls, prompt+duration forwarded to Seedance
Handler-level concerns (busy-guard, cancel-clear, cost log) are Telethon-verified.
fal_client / network are mocked — no paid calls.
"""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ════════════════════════════════ fal_media ════════════════════════════════

def _fake_fal_client(captured, video_url="https://fal.example/out.mp4"):
    mod = types.ModuleType("fal_client")

    def subscribe(endpoint, arguments=None, with_logs=False, **kw):
        captured["endpoint"] = endpoint
        captured["arguments"] = arguments
        captured["kwargs"] = kw
        return {"video": {"url": video_url}}

    mod.subscribe = subscribe
    return mod


def test_seedance_passes_timeouts_to_subscribe(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    captured = {}
    monkeypatch.setitem(sys.modules, "fal_client", _fake_fal_client(captured))
    import fal_media
    monkeypatch.setattr(fal_media, "_download_timeout", lambda url, dest: Path(dest).write_bytes(b"x" * 100_000))
    fal_media.generate_seedance_video("p", tmp_path / "ai_01.mp4", duration=5)
    kw = captured["kwargs"]
    assert kw.get("start_timeout") == fal_media.SEEDANCE_TIMEOUT_S
    assert kw.get("client_timeout") == fal_media.SEEDANCE_TIMEOUT_S


def test_seedance_rejects_tiny_download(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    monkeypatch.setitem(sys.modules, "fal_client", _fake_fal_client({}))
    import fal_media
    monkeypatch.setattr(fal_media, "_download_timeout", lambda url, dest: Path(dest).write_bytes(b"tiny"))
    dest = tmp_path / "ai_01.mp4"
    out = fal_media.generate_seedance_video("p", dest)
    assert out is None
    assert not dest.exists()                       # broken paid clip not surfaced as success


def test_seedance_download_crash_leaves_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    monkeypatch.setitem(sys.modules, "fal_client", _fake_fal_client({}))
    import fal_media

    def boom(url, dest):
        raise OSError("network down")

    monkeypatch.setattr(fal_media, "_download_timeout", boom)
    dest = tmp_path / "ai_01.mp4"
    out = fal_media.generate_seedance_video("p", dest)
    assert out is None
    assert not dest.exists()
    assert not (tmp_path / "ai_01.mp4.part").exists()   # atomic: no leftover .part


def test_kling_ready_false_without_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    import fal_media
    ok, reason = fal_media.kling_ready()
    assert ok is False and reason


def test_kling_ready_true_with_key(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "id:secret")
    import fal_media
    ok, _ = fal_media.kling_ready()
    assert ok is True            # fal_client is installed in this env


# ════════════════════════════════ director ═════════════════════════════════

class _FakeClaude:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, *, model, max_tokens, messages, system=None, **kw):
        self.calls.append({"messages": messages})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=item)])


def _plan_json(n):
    return json.dumps({"clips": [{"beat": f"b{i}", "prompt": f"Multiple shots. [wide shot] scene {i} cinematic drive"} for i in range(n)]})


def test_director_caps_to_max_clips_param():
    import ai_video_broll
    claude = _FakeClaude([_plan_json(4)])
    clips = ai_video_broll.plan_clips("s", claude, max_clips=2)
    assert len(clips) == 2


def test_director_drops_short_and_blank_prompts():
    import ai_video_broll
    bad = json.dumps({"clips": [{"prompt": "   "}, {"prompt": "x"}, {"beat": "no prompt"}]})
    claude = _FakeClaude([bad, bad])           # both attempts invalid → raise
    with pytest.raises(ai_video_broll.AiVideoError):
        ai_video_broll.plan_clips("s", claude)


def test_director_retry_includes_repair_feedback():
    import ai_video_broll
    claude = _FakeClaude(["not json at all", _plan_json(3)])
    clips = ai_video_broll.plan_clips("s", claude)
    assert len(clips) == 3
    first = claude.calls[0]["messages"][0]["content"]
    second = claude.calls[1]["messages"][0]["content"]
    assert second != first                       # not a dumb repeat
    assert "невал" in second.lower() or "invalid" in second.lower()


def test_director_retries_on_create_exception():
    import ai_video_broll
    claude = _FakeClaude([RuntimeError("transient 529"), _plan_json(3)])
    clips = ai_video_broll.plan_clips("s", claude)   # exception on 1st attempt must retry
    assert len(clips) == 3
    assert len(claude.calls) == 2


# ════════════════════════════════ engine ═══════════════════════════════════

class _SpyClaude:
    def __init__(self):
        self.create_calls = 0
        self.messages = self

    def create(self, **kw):
        self.create_calls += 1
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=_plan_json(3))])


def test_engine_preflight_fails_before_claude(monkeypatch, tmp_path):
    import ai_video_broll
    monkeypatch.setattr(ai_video_broll.fal_media, "kling_ready", lambda: (False, "FAL_KEY missing"))
    spy = _SpyClaude()
    with pytest.raises(ai_video_broll.AiVideoError):
        ai_video_broll.generate_ai_broll("s", tmp_path, claude=spy)
    assert spy.create_calls == 0                 # director never invoked when FAL unready


def test_engine_threads_max_clips_and_caps_kling_calls(monkeypatch, tmp_path):
    import ai_video_broll
    monkeypatch.setattr(ai_video_broll.fal_media, "kling_ready", lambda: (True, ""))
    seen = {}

    def director_spy(script, claude, max_clips=ai_video_broll.MAX_CLIPS, target_clips=None,
                     business_context=None):
        seen["max_clips"] = max_clips
        return [{"prompt": f"p{i}", "beat": "b"} for i in range(max_clips)]

    monkeypatch.setattr(ai_video_broll, "plan_clips", director_spy)
    calls = {"n": 0}

    def kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None):
        calls["n"] += 1
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"x")
        return str(dest)

    monkeypatch.setattr(ai_video_broll.fal_media, "generate_kling_video", kling)
    ai_video_broll.generate_ai_broll("s", tmp_path, claude=object(), duration=5, max_clips=2)
    assert seen["max_clips"] == 2
    assert calls["n"] == 2                       # never pay for more than max_clips


def test_engine_forwards_prompt_and_duration_to_kling(monkeypatch, tmp_path):
    import ai_video_broll
    monkeypatch.setattr(ai_video_broll.fal_media, "kling_ready", lambda: (True, ""))
    monkeypatch.setattr(ai_video_broll, "plan_clips",
                        lambda script, claude, max_clips=4, target_clips=None,
                        business_context=None: [{"prompt": "PROMPT_X", "beat": "b"}])
    captured = {}

    def kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None):
        captured["prompt"] = prompt
        captured["duration"] = duration
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"x")
        return str(dest)

    monkeypatch.setattr(ai_video_broll.fal_media, "generate_kling_video", kling)
    ai_video_broll.generate_ai_broll("s", tmp_path, claude=object(), duration=10, max_clips=4)
    assert captured["prompt"] == "PROMPT_X"
    assert captured["duration"] == 10


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
