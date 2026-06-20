"""TDD: переключение AI-видео B-roll на Kling 3.0 Pro + цена + без overshoot + персона.

Закрепляет изменения 2026-06-20:
  - движок генерации = fal_media.generate_kling_video (НЕ generate_seedance_video)
  - цена = KLING_PRICE_PER_SEC_USD ($0.112/сек, плоская)
  - clips_needed без +1 overshoot-буфера
  - plan_clips прокидывает business_context (персона) в промпт
LLM и сеть замоканы.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_video_broll as A  # noqa: E402


class _FakeClaude:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, *, model, max_tokens, messages, system=None, **kw):
        self.calls.append({"messages": messages})
        text = self._responses.pop(0)
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=text)])


_PLAN3 = '{"clips":[{"beat":"a","prompt":"Multiple shots. one"},' \
         '{"beat":"b","prompt":"Multiple shots. two"},' \
         '{"beat":"c","prompt":"Multiple shots. three"}]}'


# ── clips_needed: без overshoot ──────────────────────────────────────────────
def test_clips_needed_no_overshoot():
    assert A.clips_needed(30, 10) == 3       # было бы 4 со старым +1
    assert A.clips_needed(28, 10) == 3
    assert A.clips_needed(31, 10) == 4       # чуть больше 30 → 4 клипа честно
    assert A.clips_needed(0, 10) >= A.MIN_CLIPS
    # buffer всё ещё доступен явно
    assert A.clips_needed(30, 10, buffer=1) == 4


# ── цена = Kling, плоская $/сек ───────────────────────────────────────────────
def test_estimate_cost_range_uses_kling_price():
    lo, hi = A.estimate_cost_range(10)
    assert abs(lo - A.MIN_CLIPS * 10 * A.KLING_PRICE_PER_SEC_USD) < 1e-9
    assert abs(hi - A.MAX_CLIPS * 10 * A.KLING_PRICE_PER_SEC_USD) < 1e-9
    assert abs(A.KLING_PRICE_PER_SEC_USD - 0.112) < 1e-9


def test_fullscreen_plan_cost_kling_and_no_overshoot():
    plan = A.fullscreen_plan("слово " * 75)   # ~75 слов → ~30с озвучки
    import math
    expect_n = max(A.MIN_CLIPS, math.ceil(plan["est_sec"] / plan["clip_len"]))
    assert plan["n_clips"] == expect_n       # ровно ceil, без +1
    assert abs(plan["cost"] - plan["n_clips"] * plan["clip_len"] * A.KLING_PRICE_PER_SEC_USD) < 1e-9


# ── движок: generate_ai_broll зовёт Kling, НЕ Seedance ───────────────────────
def test_generate_ai_broll_calls_kling(monkeypatch, tmp_path):
    calls = {"kling": 0, "seedance": 0}

    def fake_kling(prompt, dest, duration=5, aspect="9:16"):
        calls["kling"] += 1
        return str(dest)

    def fake_seedance(*a, **k):
        calls["seedance"] += 1
        return None

    monkeypatch.setattr(A.fal_media, "seedance_ready", lambda: (True, "ok"))
    monkeypatch.setattr(A.fal_media, "generate_kling_video", fake_kling)
    monkeypatch.setattr(A.fal_media, "generate_seedance_video", fake_seedance)
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": "x", "prompt": "Multiple shots. p1"},
        {"beat": "y", "prompt": "Multiple shots. p2"},
        {"beat": "z", "prompt": "Multiple shots. p3"},
    ])

    paths, cost = A.generate_ai_broll("сценарий", tmp_path, claude=object(),
                                      duration=10, target_clips=3)
    assert calls["kling"] == 3
    assert calls["seedance"] == 0
    assert len(paths) == 3
    assert abs(cost - 3 * 10 * A.KLING_PRICE_PER_SEC_USD) < 1e-9   # $3.36


# ── персона: business_context попадает в промпт ──────────────────────────────
def test_plan_clips_injects_business_context():
    claude = _FakeClaude([_PLAN3])
    A.plan_clips("сценарий", claude, target_clips=3,
                 business_context="МАРКЕР_ПЕРСОНЫ_QWE")
    sent = claude.calls[0]["messages"][0]["content"]
    assert "МАРКЕР_ПЕРСОНЫ_QWE" in sent
    assert "сценарий" in sent


def test_plan_clips_default_persona_is_maksim():
    claude = _FakeClaude([_PLAN3])
    A.plan_clips("сценарий", claude, target_clips=3)
    sent = claude.calls[0]["messages"][0]["content"]
    assert "Максим" in sent and "Life Drive" in sent


# ── Kling API: всегда generate_audio=false (звук монтаж выкидывает + дешевле) ──
def test_kling_requests_audio_off(monkeypatch, tmp_path):
    import fal_media
    captured = {}

    fake_fal = types.SimpleNamespace(
        subscribe=lambda endpoint, **kw: (captured.update(kw.get("arguments", {})),
                                          {"video": {"url": "http://x/v.mp4"}})[1]
    )
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
    monkeypatch.setattr(fal_media, "_is_configured", lambda: True)
    monkeypatch.setattr(fal_media, "_download_timeout",
                        lambda url, part: Path(part).write_bytes(b"x" * 60000))

    out = fal_media.generate_kling_video("p", tmp_path / "c.mp4", duration=5)
    assert out is not None
    assert captured.get("generate_audio") is False
    assert captured.get("prompt") == "p"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
