"""TDD for the director's fullscreen mode (Phase 2): generate ~N beats to cover
the whole voiceover, instead of the Phase-1 "2-4 cutaways". target_clips=None
keeps Phase-1 behaviour byte-identical.
"""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeClaude:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, *, model, max_tokens, messages, system=None, **kw):
        self.calls.append({"messages": messages})
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=self._responses.pop(0))])


def _plan_json(n):
    return json.dumps({"clips": [
        {"beat": f"b{i}", "prompt": f"Multiple shots. [wide] scene {i} cinematic drive"}
        for i in range(n)
    ]})


def test_target_clips_caps_and_requests_count():
    import ai_video_broll
    claude = _FakeClaude([_plan_json(6)])
    clips = ai_video_broll.plan_clips("s", claude, target_clips=6)
    assert len(clips) == 6
    assert "6" in claude.calls[0]["messages"][0]["content"]   # target nudged into the prompt


def test_target_clips_phase1_default_unchanged():
    import ai_video_broll
    claude = _FakeClaude([_plan_json(3)])
    clips = ai_video_broll.plan_clips("s", claude)            # no target → Phase-1 "2-4" mode
    assert len(clips) == 3
    assert "РОВНО" not in claude.calls[0]["messages"][0]["content"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
