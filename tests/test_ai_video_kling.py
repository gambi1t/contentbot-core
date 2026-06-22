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

    def fake_kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None):
        calls["kling"] += 1
        calls["neg"] = negative_prompt
        return str(dest)

    def fake_seedance(*a, **k):
        calls["seedance"] += 1
        return None

    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))
    monkeypatch.setattr(A.fal_media, "generate_kling_video", fake_kling)
    monkeypatch.setattr(A.fal_media, "generate_seedance_video", fake_seedance)
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": "x", "prompt": "Multiple shots. p1", "negative_prompt": "text, logo"},
        {"beat": "y", "prompt": "Multiple shots. p2", "negative_prompt": "text, logo"},
        {"beat": "z", "prompt": "Multiple shots. p3", "negative_prompt": "text, logo"},
    ])

    paths, cost = A.generate_ai_broll("сценарий", tmp_path, claude=object(),
                                      duration=10, target_clips=3)
    assert calls["kling"] == 3
    assert calls["seedance"] == 0
    assert calls["neg"] == "text, logo"        # negative_prompt проброшен в fal-вызов
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


def test_kling_passes_negative_prompt(monkeypatch, tmp_path):
    """negative_prompt доходит до fal-аргументов (поле существует в схеме v3/pro)."""
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

    out = fal_media.generate_kling_video("p", tmp_path / "c.mp4", duration=5,
                                         negative_prompt="text, logo, deformed hands")
    assert out is not None
    assert captured.get("negative_prompt") == "text, logo, deformed hands"


# ── ретрай скачивания: транзиентный CDN-сбой не теряет оплаченный клип ─────────
def test_kling_download_retries_transient_then_succeeds(monkeypatch, tmp_path):
    """HTTP 500 на скачивании ретраится — отрендеренный (оплаченный) клип не теряется."""
    import fal_media
    fake_fal = types.SimpleNamespace(
        subscribe=lambda endpoint, **kw: {"video": {"url": "http://x/v.mp4"}})
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
    monkeypatch.setattr(fal_media, "_is_configured", lambda: True)
    monkeypatch.setattr(fal_media.time, "sleep", lambda *a: None)   # без реального backoff

    attempts = {"n": 0}

    def flaky(url, part):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Exception("HTTP Error 500: Internal Server Error")
        Path(part).write_bytes(b"x" * 60000)

    monkeypatch.setattr(fal_media, "_download_timeout", flaky)

    out = fal_media.generate_kling_video("p", tmp_path / "c.mp4", duration=5)
    assert out is not None                  # клип сохранён несмотря на 2 сбоя
    assert attempts["n"] == 3               # 2 фейла + успех на 3-й
    assert Path(out).exists()


def test_kling_download_gives_up_after_retries(monkeypatch, tmp_path):
    """Все попытки скачивания падают → None (исчерпали ретраи, без краша)."""
    import fal_media
    fake_fal = types.SimpleNamespace(
        subscribe=lambda endpoint, **kw: {"video": {"url": "http://x/v.mp4"}})
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
    monkeypatch.setattr(fal_media, "_is_configured", lambda: True)
    monkeypatch.setattr(fal_media.time, "sleep", lambda *a: None)

    n = {"n": 0}

    def always_fail(url, part):
        n["n"] += 1
        raise Exception("HTTP Error 500")

    monkeypatch.setattr(fal_media, "_download_timeout", always_fail)

    out = fal_media.generate_kling_video("p", tmp_path / "c.mp4", duration=5)
    assert out is None
    assert n["n"] == fal_media.KLING_DOWNLOAD_RETRIES    # все попытки исчерпаны


# ── #3 частичный сбой: персист плана + добор недостающих клипов ───────────────
def test_generate_ai_broll_persists_plan(monkeypatch, tmp_path):
    """generate_ai_broll сохраняет план клипов (plan.json) — чтобы потом
    можно было ДОБРАТЬ упавший клип по тому же промпту без вызова режиссёра."""
    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))
    monkeypatch.setattr(
        A.fal_media, "generate_kling_video",
        lambda prompt, dest, duration=5, aspect="9:16", negative_prompt=None:
            (Path(dest).write_bytes(b"x"), str(dest))[1])
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": "x", "prompt": "Multiple shots. p1", "negative_prompt": "text"},
        {"beat": "y", "prompt": "Multiple shots. p2", "negative_prompt": "text"}])

    A.generate_ai_broll("сценарий", tmp_path, claude=object(), duration=10, target_clips=2)
    import json as _json
    plan = _json.loads((tmp_path / A.CLIPS_SUBDIR / "plan.json").read_text(encoding="utf-8"))
    assert plan["duration"] == 10
    assert len(plan["clips"]) == 2
    assert plan["clips"][1]["prompt"] == "Multiple shots. p2"
    assert plan["clips"][1]["i"] == 2


def test_regen_fills_only_missing_clips(monkeypatch, tmp_path):
    """regen_ai_clips добирает ТОЛЬКО недостающие клипы (нет файла) по плану."""
    import json as _json
    clips_dir = tmp_path / A.CLIPS_SUBDIR
    clips_dir.mkdir(parents=True)
    (clips_dir / "plan.json").write_text(_json.dumps({
        "duration": 10,
        "clips": [{"i": 1, "prompt": "Multiple shots. one", "negative_prompt": "text"},
                  {"i": 2, "prompt": "Multiple shots. two", "negative_prompt": "text"}],
    }), encoding="utf-8")
    (clips_dir / "ai_01.mp4").write_bytes(b"x")   # клип 1 уже есть, 2 — пропущен

    rendered = []

    def fake_kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None):
        rendered.append(prompt)
        Path(dest).write_bytes(b"y")
        return str(dest)

    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))
    monkeypatch.setattr(A.fal_media, "generate_kling_video", fake_kling)

    new_paths, cost = A.regen_ai_clips(tmp_path)
    assert len(new_paths) == 1                       # добрали только пропущенный
    assert rendered == ["Multiple shots. two"]       # именно клип 2 по плану
    assert (clips_dir / "ai_02.mp4").exists()
    assert abs(cost - 1 * 10 * A.KLING_PRICE_PER_SEC_USD) < 1e-9


def test_regen_nothing_missing_is_noop(monkeypatch, tmp_path):
    """Все клипы на месте → regen ничего не рендерит (0 трат)."""
    import json as _json
    clips_dir = tmp_path / A.CLIPS_SUBDIR
    clips_dir.mkdir(parents=True)
    (clips_dir / "plan.json").write_text(_json.dumps({
        "duration": 10, "clips": [{"i": 1, "prompt": "one"}]}), encoding="utf-8")
    (clips_dir / "ai_01.mp4").write_bytes(b"x")
    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))
    monkeypatch.setattr(A.fal_media, "generate_kling_video",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("не должен вызываться")))
    new_paths, cost = A.regen_ai_clips(tmp_path)
    assert new_paths == [] and cost == 0.0


# ── #2 инкремент 2: голос-правка промпта клипа (revise_clip_prompt) ───────────
def test_revise_clip_prompt_updates_plan(tmp_path):
    import json as _json
    clips_dir = tmp_path / A.CLIPS_SUBDIR
    clips_dir.mkdir(parents=True)
    (clips_dir / "plan.json").write_text(_json.dumps({
        "duration": 10,
        "clips": [{"i": 1, "prompt": "Multiple shots. old prompt one here", "negative_prompt": "text"},
                  {"i": 2, "prompt": "Multiple shots. old prompt two here", "negative_prompt": "text"}],
    }), encoding="utf-8")

    fake = _FakeClaude(['{"prompt":"Multiple shots. REVISED clip two, no people, brighter phone on calm desk","negative_prompt":"text, people, watermark"}'])
    new = A.revise_clip_prompt(tmp_path, 2, "убери людей, телефон ярче", claude=fake)
    assert new and "REVISED" in new
    plan = _json.loads((clips_dir / "plan.json").read_text(encoding="utf-8"))
    assert "REVISED" in plan["clips"][1]["prompt"]            # клип 2 переписан
    assert plan["clips"][0]["prompt"].endswith("one here")   # клип 1 не тронут
    assert plan["clips"][1]["negative_prompt"] == "text, people, watermark"


def test_revise_clip_prompt_keeps_old_on_bad_llm(tmp_path):
    import json as _json
    clips_dir = tmp_path / A.CLIPS_SUBDIR
    clips_dir.mkdir(parents=True)
    (clips_dir / "plan.json").write_text(_json.dumps({
        "duration": 10,
        "clips": [{"i": 1, "prompt": "Multiple shots. original good prompt", "negative_prompt": "text"}]}),
        encoding="utf-8")
    fake = _FakeClaude(['{"prompt":"short"}'])               # < MIN_PROMPT_LEN
    out = A.revise_clip_prompt(tmp_path, 1, "что-то", claude=fake)
    assert out is None
    plan = _json.loads((clips_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan["clips"][0]["prompt"] == "Multiple shots. original good prompt"   # старый цел


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
