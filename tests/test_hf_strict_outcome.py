"""D Steps 5-6 (GPT-5): strict-mode outcome после MAX_FIX_ROUNDS + quality-report.

Step 5: render-ошибки → хард-фейл (нет mp4); strict+layout-blocking, рендер ОК →
best-effort (отдать ролик с предупреждением, не валить из-за вёрстки).
Step 6: компактный quality-report (scenes/blocking/advisory/fix_rounds/best_effort).

Run: python tests/test_hf_strict_outcome.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as hf  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _blk(): return {"scene_06.html": [{"type": "tiny_text", "severity": "blocking"}]}


def test_outcome_render_fail(errors):
    print("\n-- render-ошибки → render_fail (хард-фейл, нет mp4) --")
    _assert(hf._max_rounds_outcome(["scene_01.html: ffmpeg"], "strict", _blk()) == "render_fail",
            "render-errors доминируют → render_fail", errors)
    _assert(hf._max_rounds_outcome(["x: err"], "advisory", {}) == "render_fail",
            "render-errors → render_fail в любом режиме", errors)


def test_outcome_best_effort(errors):
    print("\n-- strict + layout-blocking, рендер ОК → best_effort (не валим ролик) --")
    _assert(hf._max_rounds_outcome([], "strict", _blk()) == "best_effort",
            "strict + blocking, без render-errors → best_effort", errors)


def test_outcome_clean_or_nonstrict(errors):
    print("\n-- advisory/off ИЛИ нет blocking → clean (не валим, не warning) --")
    _assert(hf._max_rounds_outcome([], "advisory", _blk()) == "clean",
            "advisory + blocking → clean (advisory не блокирует)", errors)
    _assert(hf._max_rounds_outcome([], "strict", {}) == "clean",
            "strict без blocking → clean", errors)


def test_quality_report_shape(errors):
    print("\n-- quality-report: scenes/blocking/advisory/fix_rounds/best_effort --")
    layout = {
        "scene_03.html": [{"type": "tiny_text", "severity": "blocking"},
                          {"type": "crowding", "severity": "advisory"}],
        "scene_06.html": [{"type": "offscreen", "severity": "blocking"}],
    }
    blocking = {
        "scene_03.html": [{"type": "tiny_text", "severity": "blocking"}],
        "scene_06.html": [{"type": "offscreen", "severity": "blocking"}],
    }
    qr = hf._build_quality_report(9, layout, blocking, fix_rounds=1, best_effort=True)
    _assert(qr["scenes"] == 9, "scenes=9", errors)
    _assert(qr["blocking"] == {"tiny_text": 1, "offscreen": 1}, f"blocking по типам: {qr['blocking']}", errors)
    _assert(qr["advisory"] == {"crowding": 1}, f"advisory по типам: {qr['advisory']}", errors)
    _assert(qr["fix_rounds"] == 1 and qr["best_effort"] is True, "fix_rounds + best_effort", errors)
    _assert(qr["regenerated"] == ["scene_03.html", "scene_06.html"], "regenerated = blocking-сцены", errors)


def test_quality_report_clean(errors):
    print("\n-- чистый прогон → пустые blocking/advisory --")
    qr = hf._build_quality_report(8, {}, {}, fix_rounds=0, best_effort=False)
    _assert(qr["blocking"] == {} and qr["advisory"] == {} and qr["regenerated"] == [],
            "чисто → пусто", errors)


def main():
    print("=" * 60 + "\nHF strict outcome + quality report (Steps 5-6)\n" + "=" * 60)
    errors = []
    for fn in (test_outcome_render_fail, test_outcome_best_effort, test_outcome_clean_or_nonstrict,
               test_quality_report_shape, test_quality_report_clean):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all strict-outcome tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
