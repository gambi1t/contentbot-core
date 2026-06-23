"""D Step 4 (GPT-5 review): blocking_layout_subset — фильтр layout-findings из
детектора на BLOCKING-подмножество (для fix-round). Остальное остаётся advisory
(только в лог).

Контракт (GPT-5 reco):
  BLOCKING:
    - offscreen с severity=blocking (semantic text/card, не decor)
    - tiny_text с severity=blocking (hero/heading/body/cta; caption — advisory)
    - overlap с severity=blocking (hard formula + оба semantic-role)
  ADVISORY (НЕ блокирует):
    - crowding (всегда)
    - overlap с severity=advisory
    - tiny_text для caption
    - offscreen для decor

Также проверяем: главный цикл триггерит fix-round при наличии blocking ИЛИ
render_errors (раньше только render_errors).

Run: python tests/test_hf_blocking_subset.py
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


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


# Фикстуры issues — то, что вернёт обновлённый детектор (Step 2-3)
def _tiny_hero(): return {"type": "tiny_text", "severity": "blocking", "role": "hero",
                           "text": "Подпишись", "px": 42.0, "min": 60}
def _tiny_caption_adv(): return {"type": "tiny_text", "severity": "advisory", "role": "caption",
                                  "text": "не пропусти", "px": 16.0, "min": 18}
def _offscreen_text(): return {"type": "offscreen", "severity": "blocking",
                                "kind": "text", "role": "heading", "text": "Заголовок", "edge": "right"}
def _offscreen_decor(): return {"type": "offscreen", "severity": "advisory",
                                 "kind": "card", "role": "decor", "text": "", "edge": "left"}
def _hard_overlap(): return {"type": "overlap", "severity": "blocking",
                              "a": "Контент", "b": "Завод", "roleA": "hero", "roleB": "hero",
                              "overlapPx": 1200, "hardThresholdPx": 600}
def _soft_overlap(): return {"type": "overlap", "severity": "advisory",
                              "a": "A", "b": "B", "overlapPx": 80, "hardThresholdPx": 300}
def _crowding(): return {"type": "crowding", "severity": "advisory",
                          "a": "слева", "b": "справа", "gapPx": 18}


def test_classify_blocking_keeps_blocking(errors):
    print("\n-- blocking-issues остаются (tiny_hero, offscreen text, hard overlap) --")
    layout = {
        "scene_03.html": [_tiny_hero(), _crowding()],
        "scene_05.html": [_offscreen_text(), _offscreen_decor()],
        "scene_07.html": [_hard_overlap(), _soft_overlap()],
    }
    blocking = hf._blocking_layout_subset(layout)
    _assert(len(blocking) == 3, f"3 сцены с blocking, got {len(blocking)}", errors)
    _assert(len(blocking["scene_03.html"]) == 1 and blocking["scene_03.html"][0]["type"] == "tiny_text",
            "scene_03: только tiny_hero (crowding отсеян)", errors)
    _assert(len(blocking["scene_05.html"]) == 1 and blocking["scene_05.html"][0]["role"] == "heading",
            "scene_05: только offscreen text (decor отсеян)", errors)
    _assert(len(blocking["scene_07.html"]) == 1 and blocking["scene_07.html"][0]["type"] == "overlap",
            "scene_07: только hard overlap (soft отсеян)", errors)


def test_classify_drops_advisory_only(errors):
    print("\n-- сцена с ТОЛЬКО advisory → не в blocking subset (нет ключа) --")
    layout = {
        "scene_02.html": [_crowding(), _soft_overlap(), _tiny_caption_adv(), _offscreen_decor()],
    }
    blocking = hf._blocking_layout_subset(layout)
    _assert("scene_02.html" not in blocking, f"только advisory → не блокирует, got {list(blocking)}", errors)


def test_classify_empty_input(errors):
    print("\n-- пустой/None layout → пустой subset --")
    _assert(hf._blocking_layout_subset({}) == {}, "{} → {}", errors)
    _assert(hf._blocking_layout_subset(None) == {}, "None → {}", errors)


def test_classify_unknown_type_treated_advisory(errors):
    print("\n-- неизвестный type без severity → не блокирует (safe-default) --")
    layout = {"scene_01.html": [{"type": "weird_new_thing", "text": "?"}]}
    _assert(hf._blocking_layout_subset(layout) == {}, "unknown без severity → advisory", errors)


def test_classify_explicit_severity_wins(errors):
    print("\n-- severity='blocking' в issue форсит включение независимо от type --")
    layout = {"scene_01.html": [{"type": "future_check", "severity": "blocking", "text": "x"}]}
    blocking = hf._blocking_layout_subset(layout)
    _assert(blocking and "scene_01.html" in blocking, "явный blocking → попадает", errors)


# ── интеграция: главный цикл триггерит fix-round при blocking ИЛИ render-err ──


def test_needs_fix_round_render_errors_always(errors):
    print("\n-- _needs_fix_round: render-errors всегда триггер (вне зависимости от gate) --")
    layout_blocking = {"scene_01.html": [_tiny_hero()]}
    # В strict-режиме blocking-layout уже триггерит fix (отдельные тесты ниже).
    # Здесь — общий контракт: render-errors доминируют.
    _assert(hf._needs_fix_round({}, []) is False, "пусто → не fix", errors)
    _assert(hf._needs_fix_round({}, ["scene_01.html: ffmpeg error"]) is True, "render error → fix", errors)
    _assert(hf._needs_fix_round(layout_blocking, ["x: err"]) is True, "blocking + errors → fix", errors)


# ── env-gate (off/advisory/strict) уважается ─────────────────────────────────


def test_strict_gate_uses_blocking(errors):
    print("\n-- mode=strict → blocking subset идёт в fix-round; advisory просто лог --")
    os.environ["HF_LAYOUT_GATE"] = "strict"
    layout = {"scene_01.html": [_tiny_hero()]}
    _assert(hf._needs_fix_round(layout, []) is True, "strict + blocking → fix", errors)


def test_off_gate_ignores_layout(errors):
    print("\n-- mode=off → layout полностью игнорируется (даже blocking) --")
    os.environ["HF_LAYOUT_GATE"] = "off"
    layout = {"scene_01.html": [_tiny_hero()]}
    _assert(hf._needs_fix_round(layout, []) is False,
            "off + blocking → НЕ fix (только render-errors двигают)", errors)
    _assert(hf._needs_fix_round(layout, ["x: err"]) is True, "off + errors → fix всё равно", errors)


def test_advisory_gate_layout_in_log_not_fix(errors):
    print("\n-- mode=advisory (default) → layout НЕ блокирует, только render-errors --")
    os.environ["HF_LAYOUT_GATE"] = "advisory"
    layout = {"scene_01.html": [_tiny_hero()]}
    _assert(hf._needs_fix_round(layout, []) is False, "advisory + blocking-issue → НЕ fix", errors)
    _assert(hf._needs_fix_round(layout, ["x: err"]) is True, "advisory + render-err → fix", errors)


def main() -> int:
    print("=" * 60 + "\nHF layout-gate Step 4: blocking subset + fix-round trigger\n" + "=" * 60)
    errors: list = []
    _orig = os.environ.get("HF_LAYOUT_GATE")
    try:
        for fn in (test_classify_blocking_keeps_blocking, test_classify_drops_advisory_only,
                   test_classify_empty_input, test_classify_unknown_type_treated_advisory,
                   test_classify_explicit_severity_wins, test_needs_fix_round_render_errors_always,
                   test_strict_gate_uses_blocking, test_off_gate_ignores_layout,
                   test_advisory_gate_layout_in_log_not_fix):
            fn(errors)
    finally:
        if _orig is None:
            os.environ.pop("HF_LAYOUT_GATE", None)
        else:
            os.environ["HF_LAYOUT_GATE"] = _orig
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all blocking-subset tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
