"""TDD (срез C): per-tenant персона режиссёра ai_video + cost-guard по длительности.

- _default_persona(): panferov → Артём/AI-студия (без картинга/Life Drive); иначе → Максим.
- cost-guard: суммарная длительность ролика ≤ AI_VIDEO_MAX_DURATION_SEC (дефолт 60с)
  и в fullscreen_plan (оценка), и жёстким backstop в generate_ai_broll.
LLM и сеть замоканы.
"""
import sys
import types
from pathlib import Path

import pytest  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_video_broll as A  # noqa: E402
import tenant  # noqa: E402


class _FakeClaude:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, *, model, max_tokens, messages, system=None, **kw):
        self.calls.append({"messages": messages})
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text=self._responses.pop(0))])


_PLAN3 = '{"clips":[{"beat":"a","prompt":"Multiple shots. one"},' \
         '{"beat":"b","prompt":"Multiple shots. two"},' \
         '{"beat":"c","prompt":"Multiple shots. three"}]}'


# ── персона per-tenant ───────────────────────────────────────────────────────
def test_default_persona_panferov(monkeypatch):
    monkeypatch.setattr(tenant, "active_tenant_id", lambda: "panferov")
    p = A._default_persona()
    assert "Артём" in p and ("AI-студи" in p or "ИИ для предприним" in p)
    for bad in ("картинг", "глэмпинг", "Life Drive", "Тюмен"):
        assert bad not in p, f"panferov persona must not contain «{bad}»"


def test_default_persona_maksim_and_default(monkeypatch):
    for tid in ("maksim", "default"):
        monkeypatch.setattr(tenant, "active_tenant_id", lambda t=tid: t)
        p = A._default_persona()
        assert "Максим" in p and "Life Drive" in p, f"tenant={tid} → Максим"


def test_default_persona_fallback_on_error(monkeypatch):
    def boom():
        raise RuntimeError("no tenant")
    monkeypatch.setattr(tenant, "active_tenant_id", boom)
    assert "Максим" in A._default_persona()   # деградирует, не падает


def test_plan_clips_uses_panferov_persona(monkeypatch):
    monkeypatch.setattr(tenant, "active_tenant_id", lambda: "panferov")
    claude = _FakeClaude([_PLAN3])
    A.plan_clips("сценарий", claude, target_clips=3)   # без business_context
    sent = claude.calls[0]["messages"][0]["content"]
    assert "Артём" in sent
    assert "картинг" not in sent and "Life Drive" not in sent


# ── cost-guard ───────────────────────────────────────────────────────────────
def test_max_duration_default_60():
    assert A.AI_VIDEO_MAX_DURATION_SEC == 60


def test_max_clips_for_budget():
    assert A._max_clips_for_budget(10) == 6     # 60/10
    assert A._max_clips_for_budget(5) == 12     # 60/5
    assert A._max_clips_for_budget(0) >= 1      # без деления на 0


def test_fullscreen_plan_capped_on_long_script():
    plan = A.fullscreen_plan("слово " * 600)    # ~240с озвучки → 24 клипа без cap
    assert plan["n_clips"] == A._max_clips_for_budget(plan["clip_len"])  # = 6
    assert abs(plan["cost"] - 6 * plan["clip_len"] * A.KLING_PRICE_PER_SEC_USD) < 1e-9


def test_fullscreen_plan_short_unaffected():
    plan = A.fullscreen_plan("слово " * 75)     # ~30с → 3 клипа, ниже cap
    assert plan["n_clips"] == 3


def test_generate_ai_broll_cost_guard_caps(monkeypatch, tmp_path):
    calls = {"kling": 0}

    def fake_kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None):
        calls["kling"] += 1
        return str(dest)

    monkeypatch.setattr(A.fal_media, "seedance_ready", lambda: (True, "ok"))
    monkeypatch.setattr(A.fal_media, "generate_kling_video", fake_kling)
    # директор вернул 10 клипов — cost-guard должен срезать до 6 (60/10)
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": str(i), "prompt": f"Multiple shots. p{i}"} for i in range(10)])

    paths, cost = A.generate_ai_broll("сценарий", tmp_path, claude=object(),
                                      duration=10, target_clips=10)
    assert calls["kling"] == 6                  # срезано до потолка
    assert len(paths) == 6
    assert abs(cost - 6 * 10 * A.KLING_PRICE_PER_SEC_USD) < 1e-9   # $6.72
