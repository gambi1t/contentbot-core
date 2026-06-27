"""TDD: Fix #5 — AI-видео audio-first длительность (микс 5/10с под реальную озвучку).

Баг (прод 25.06): число клипов из ЧИСЛА СЛОВ (≈150 wpm) ДО озвучки → 2×10с=20с
видео при озвучке 14.6с → ~6с оплаченного Kling впустую. Фикс: озвучку первой →
ffprobe реальной длины → план клипов под факт, микшируя 5с/10с с минимальным
остатком (14.6с → [10,5]=15, НЕ [10,10]=20).

Контракт generate_ai_broll с дефолтом clip_durations=None НЕ меняется (селфи цел).

Запуск: python -m pytest tests/test_ai_video_audiofirst.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_video_broll as A  # noqa: E402


# ── plan_clip_durations: микс 5/10 с минимальным остатком ─────────────────────

def test_mix_small_tail_uses_5():
    # 14.6с → один 10 + один 5 (=15, остаток 0.4), НЕ два 10 (=20)
    assert A.plan_clip_durations(14.6) == [10, 5]


def test_mix_exact_tens():
    assert A.plan_clip_durations(30) == [10, 10, 10]   # ровно, без 5с-хвоста
    assert A.plan_clip_durations(20) == [10, 10]


def test_mix_big_tail_uses_10():
    # хвост >5с → ещё 10с (23с → 10+10+5=25, остаток 2)
    assert A.plan_clip_durations(23) == [10, 10, 5]
    # 26с → хвост 6 (>5) → 10+10+10=30
    assert A.plan_clip_durations(26) == [10, 10, 10]


def test_mix_respects_min_clips():
    # короткая озвучка не должна давать 1 клип (визуальное разнообразие)
    out = A.plan_clip_durations(8)
    assert len(out) >= A.MIN_CLIPS
    assert sum(out) >= 8                       # покрывает озвучку


def test_mix_empty_fallback():
    out = A.plan_clip_durations(0)
    assert len(out) >= A.MIN_CLIPS and all(d in (5, 10) for d in out)


def test_mix_respects_cost_guard(monkeypatch):
    # длинная озвучка не разгоняет трату выше потолка секунд/прогон
    monkeypatch.setattr(A, "AI_VIDEO_MAX_DURATION_SEC", 30)
    out = A.plan_clip_durations(100, max_total=30)
    assert sum(out) <= 30


def test_only_valid_kling_durations():
    for target in (3, 7, 12.3, 14.6, 27, 44):
        assert all(d in (5, 10) for d in A.plan_clip_durations(target)), target


# ── fullscreen_plan_from_duration: оценка от РЕАЛЬНОЙ длины ────────────────────

def test_plan_from_duration_fields():
    plan = A.fullscreen_plan_from_duration(14.6)
    assert plan["durations"] == [10, 5]
    assert plan["n_clips"] == 2
    assert plan["total_sec"] == 15
    assert abs(plan["est_sec"] - 14.6) < 1e-9
    assert abs(plan["cost"] - 15 * A.KLING_PRICE_PER_SEC_USD) < 1e-9   # от факта, не слов


# ── generate_ai_broll: per-clip длины (микс) ──────────────────────────────────

def test_generate_per_clip_durations(monkeypatch, tmp_path):
    calls = []

    def fake_kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None, errors_out=None):
        calls.append(duration)
        Path(dest).write_bytes(b"x")
        return str(dest)

    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))
    monkeypatch.setattr(A.fal_media, "generate_kling_video", fake_kling)
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": "a", "prompt": "Multiple shots. p1", "negative_prompt": "text"},
        {"beat": "b", "prompt": "Multiple shots. p2", "negative_prompt": "text"}])

    paths, cost = A.generate_ai_broll("сценарий", tmp_path, claude=object(),
                                      clip_durations=[10, 5])
    assert calls == [10, 5], f"per-clip длины в Kling-вызовах: {calls}"
    assert len(paths) == 2
    assert abs(cost - 15 * A.KLING_PRICE_PER_SEC_USD) < 1e-9   # 10+5=15с


def test_generate_per_clip_persists_durations(monkeypatch, tmp_path):
    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))
    monkeypatch.setattr(
        A.fal_media, "generate_kling_video",
        lambda prompt, dest, duration=5, aspect="9:16", negative_prompt=None, errors_out=None:
            (Path(dest).write_bytes(b"x"), str(dest))[1])
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": "a", "prompt": "Multiple shots. p1", "negative_prompt": "text"},
        {"beat": "b", "prompt": "Multiple shots. p2", "negative_prompt": "text"}])

    A.generate_ai_broll("сценарий", tmp_path, claude=object(), clip_durations=[10, 5])
    import json as _json
    plan = _json.loads((tmp_path / A.CLIPS_SUBDIR / "plan.json").read_text(encoding="utf-8"))
    assert plan["clips"][0]["duration"] == 10
    assert plan["clips"][1]["duration"] == 5     # per-clip длина сохранена для regen


def test_backward_compat_uniform_unchanged(monkeypatch, tmp_path):
    # без clip_durations — старое поведение (единый duration), как зовёт селфи
    calls = []

    def fake_kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None, errors_out=None):
        calls.append(duration)
        Path(dest).write_bytes(b"x")
        return str(dest)

    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))
    monkeypatch.setattr(A.fal_media, "generate_kling_video", fake_kling)
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": "x", "prompt": "Multiple shots. p1", "negative_prompt": "t"},
        {"beat": "y", "prompt": "Multiple shots. p2", "negative_prompt": "t"},
        {"beat": "z", "prompt": "Multiple shots. p3", "negative_prompt": "t"}])

    paths, cost = A.generate_ai_broll("сц", tmp_path, claude=object(),
                                      duration=10, target_clips=3)
    assert calls == [10, 10, 10]
    assert abs(cost - 3 * 10 * A.KLING_PRICE_PER_SEC_USD) < 1e-9


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
