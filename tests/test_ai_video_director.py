"""TDD for ai_video_broll.plan_clips — the LLM "director" of the AI-video engine.

Phase 1, узел 2. Turns a flat voiceover script into 2-4 cinematic Seedance
prompts. Reuses the SubscriptionClient transport (injected as `claude`, like
the rest of the bot) and the HyperFrames "LLM plans, code validates, retry"
pattern. This test pins the CONTRACT (output shape, count bounds, fence
tolerance, retry, hard-fail) — NOT the exact wording of the approved prompt,
which lives as a string constant and is freely tweakable. The LLM is mocked.
"""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeClaude:
    """Mimics SubscriptionClient: .messages.create(...) -> obj with .content[0].text.

    Feeds canned response texts in order, one per create() call, and records
    every call so tests can assert what the director sent.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, *, model, max_tokens, messages, system=None, **kw):
        self.calls.append({"model": model, "messages": messages, "system": system})
        text = self._responses.pop(0)
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=text)])


def _plan_json(n):
    return json.dumps({
        "clips": [
            {"beat": f"бит {i}", "prompt": f"Multiple shots. [wide shot] scene {i}"}
            for i in range(n)
        ]
    })


# ── happy path / contract ────────────────────────────────────────────────────

def test_director_returns_clip_plans():
    import ai_video_broll
    claude = _FakeClaude([_plan_json(3)])
    clips = ai_video_broll.plan_clips("сценарий про бизнес", claude)
    assert len(clips) == 3
    assert all(c["prompt"].startswith("Multiple shots.") for c in clips)
    assert all(c.get("beat") for c in clips)


def test_director_clips_carry_negative_prompt():
    """Контракт v2: каждый клип несёт negative_prompt; если режиссёр его не дал —
    подставляется HOUSE_NEGATIVE (жёсткий запрет текста/артефактов в кадре)."""
    import ai_video_broll
    claude = _FakeClaude([_plan_json(3)])   # _plan_json НЕ кладёт negative_prompt
    clips = ai_video_broll.plan_clips("сценарий", claude)
    assert all(c.get("negative_prompt") for c in clips)                 # не пусто
    assert all(c["negative_prompt"] == ai_video_broll.HOUSE_NEGATIVE for c in clips)
    assert "text" in ai_video_broll.HOUSE_NEGATIVE                      # главный запрет — текст


def test_director_preserves_clip_negative_prompt():
    """Если режиссёр дал свой negative_prompt — он сохраняется, не затирается."""
    import ai_video_broll
    raw = json.dumps({"clips": [
        {"beat": f"b{i}", "prompt": f"Multiple shots. p{i}",
         "negative_prompt": "text, logo, custom-neg"} for i in range(2)
    ]})
    claude = _FakeClaude([raw])
    clips = ai_video_broll.plan_clips("s", claude)
    assert clips[0]["negative_prompt"] == "text, logo, custom-neg"


def test_director_passes_script_into_prompt():
    import ai_video_broll
    claude = _FakeClaude([_plan_json(2)])
    ai_video_broll.plan_clips("УНИКАЛЬНЫЙ_МАРКЕР_СЦЕНАРИЯ", claude)
    sent = claude.calls[0]["messages"][0]["content"]
    assert "УНИКАЛЬНЫЙ_МАРКЕР_СЦЕНАРИЯ" in sent


# ── robustness against real LLM output quirks ────────────────────────────────

def test_director_strips_markdown_fences():
    import ai_video_broll
    fenced = "```json\n" + _plan_json(2) + "\n```"
    claude = _FakeClaude([fenced])
    clips = ai_video_broll.plan_clips("s", claude)
    assert len(clips) == 2


def test_director_clamps_to_max_four():
    import ai_video_broll
    claude = _FakeClaude([_plan_json(6)])
    clips = ai_video_broll.plan_clips("s", claude)
    assert len(clips) == 4


# ── retry + hard fail ────────────────────────────────────────────────────────

def test_director_retries_then_succeeds():
    import ai_video_broll
    claude = _FakeClaude(["not json at all", _plan_json(3)])
    clips = ai_video_broll.plan_clips("s", claude)
    assert len(clips) == 3
    assert len(claude.calls) == 2


def test_director_raises_after_all_attempts():
    import ai_video_broll
    claude = _FakeClaude(["garbage", "still garbage"])
    with pytest.raises(ai_video_broll.AiVideoError):
        ai_video_broll.plan_clips("s", claude)


def test_director_rejects_too_few_clips():
    import ai_video_broll
    claude = _FakeClaude([_plan_json(1), _plan_json(1)])
    with pytest.raises(ai_video_broll.AiVideoError):
        ai_video_broll.plan_clips("s", claude)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
