"""TDD: 2-фазная генерация HyperFrames (storyboard → validate → build).

Контекст (синтез DR + ChatGPT, 1 июня): главная боль — монотонность. Решение —
не «один claude -p на 6 HTML», а ФАЗА 1 (Claude пишет storyboard.json) →
machine-валидация (storyboard_validator как ГЕЙТ) → ФАЗА 2 (Claude пишет HTML
по утверждённому storyboard). + AUTO_APPROVE (обойти approval-gate скилла,
иначе AskUserQuestion-зависание — ловили в эксперименте 1 июня).

Run: python tests/test_hyperframes_two_phase.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as H  # noqa: E402
import storyboard_validator as SV  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _golden_storyboard():
    rows = [
        ("scene_01", "hero_number", "snap_reveal", "swiss_pulse", "hero", "balanced"),
        ("scene_02", "cashflow_timeline", "path_draw", "data_drift", "medium", "dense"),
        ("scene_03", "reserve_gauge", "counter_build", "shadow_cut", "hero", "sparse"),
        ("scene_04", "before_after_cards", "card_flip", "velvet_standard", "medium", "balanced"),
        ("scene_05", "checklist", "vertical_stack", "soft_signal", "compact", "balanced"),
        ("scene_06", "final_cta", "kinetic_type", "maximalist_type", "hero", "sparse"),
    ]
    return {"version": "1.0", "scenes": [
        {"id": r[0], "business_archetype": r[1], "hf_technique": "svg_path_drawing",
         "visual_style": r[3], "motion_family": r[2], "scale_profile": r[4], "density": r[5],
         "primary_text": "Короткий текст сцены",
         "script_beat": "Фрагмент сценария про финансовый резерв сезонного бизнеса.",
         "reason": "Этот архетип лучше всего иллюстрирует данный момент сценария."}
        for r in rows
    ]}


def _bad_storyboard():
    sb = _golden_storyboard()
    sb["scenes"][1]["business_archetype"] = sb["scenes"][0]["business_archetype"]  # соседний повтор
    return sb


# ── 1. storyboard-промпт ─────────────────────────────────────────────────
def test_storyboard_prompt(errors):
    print("\n-- _build_storyboard_prompt содержит контракт фазы 1 --")
    _assert(hasattr(H, "_build_storyboard_prompt"), "_build_storyboard_prompt есть", errors)
    if not hasattr(H, "_build_storyboard_prompt"):
        return
    p = H._build_storyboard_prompt("СЦЕНАРИЙ ТЕКСТ")
    low = p.lower()
    _assert("storyboard.json" in low, "требует storyboard.json", errors)
    _assert("business_archetype" in low, "упоминает business_archetype", errors)
    _assert("reference_pack" in low, "ссылается на reference_pack", errors)
    _assert("СЦЕНАРИЙ ТЕКСТ" in p, "сценарий подставлен", errors)
    # автономность (обход approval-gate)
    _assert("автоном" in low or "auto_approve" in low or "не жди" in low or "не спрашивай" in low,
            "автономный режим (обход approval-gate)", errors)
    # фаза 1 НЕ пишет HTML
    _assert("не пиши html" in low or "без html" in low or "не создавай scene" in low,
            "явно НЕ писать HTML в фазе 1", errors)
    # запрет AskUserQuestion
    _assert("askuserquestion" in low or "не задавай вопрос" in low or "не используй вопрос" in low,
            "запрет интерактивных вопросов", errors)


# ── 2. fix-промпт ────────────────────────────────────────────────────────
def test_storyboard_fix_prompt(errors):
    print("\n-- _build_storyboard_fix_prompt включает ошибки --")
    if not hasattr(H, "_build_storyboard_fix_prompt"):
        _assert(False, "_build_storyboard_fix_prompt есть", errors)
        return
    p = H._build_storyboard_fix_prompt(["scene_02: повтор архетипа", "мало разнообразия"])
    _assert("scene_02" in p and "разнообразия" in p, "ошибки переданы в fix-промпт", errors)
    _assert("storyboard.json" in p.lower(), "просит перезаписать storyboard.json", errors)


# ── 3. _read_storyboard ──────────────────────────────────────────────────
def test_read_storyboard_exists(errors):
    print("\n-- _read_storyboard есть --")
    _assert(hasattr(H, "_read_storyboard"), "_read_storyboard есть", errors)


# ── 4. _run_storyboard_phase — валидация + fix-rounds ────────────────────
def test_phase_valid_first(errors):
    print("\n-- storyboard валиден сразу → 1 вызов claude --")
    if not hasattr(H, "_run_storyboard_phase"):
        _assert(False, "_run_storyboard_phase есть", errors)
        return
    calls = {"n": 0}
    with patch.object(H, "_run_claude", side_effect=lambda p: calls.__setitem__("n", calls["n"] + 1) or 0.0), \
         patch.object(H, "_read_storyboard", return_value=_golden_storyboard()):
        sb, cost = H._run_storyboard_phase("script")
    _assert(calls["n"] == 1, f"1 вызов claude (got {calls['n']})", errors)
    _assert(sb and sb.get("scenes"), "вернул storyboard", errors)


def test_phase_invalid_then_valid(errors):
    print("\n-- невалиден → fix → валиден (2 вызова) --")
    calls = {"n": 0}
    reads = [_bad_storyboard(), _golden_storyboard()]
    with patch.object(H, "_run_claude", side_effect=lambda p: calls.__setitem__("n", calls["n"] + 1) or 0.0), \
         patch.object(H, "_read_storyboard", side_effect=lambda: reads.pop(0)):
        sb, cost = H._run_storyboard_phase("script")
    _assert(calls["n"] == 2, f"2 вызова (initial + 1 fix), got {calls['n']}", errors)


def test_phase_always_invalid_raises(errors):
    print("\n-- всегда невалиден → raise после MAX_STORYBOARD_ATTEMPTS --")
    calls = {"n": 0}
    with patch.object(H, "_run_claude", side_effect=lambda p: calls.__setitem__("n", calls["n"] + 1) or 0.0), \
         patch.object(H, "_read_storyboard", return_value=_bad_storyboard()):
        raised = False
        try:
            H._run_storyboard_phase("script")
        except H.HyperFramesBrollError:
            raised = True
    _assert(raised, "поднял HyperFramesBrollError", errors)
    _assert(calls["n"] == H.MAX_STORYBOARD_ATTEMPTS,
            f"ровно MAX_STORYBOARD_ATTEMPTS вызовов (got {calls['n']})", errors)


def test_phase_missing_file_raises(errors):
    print("\n-- storyboard.json не создан (None) → fix-rounds → raise --")
    with patch.object(H, "_run_claude", return_value=0.0), \
         patch.object(H, "_read_storyboard", return_value=None):
        raised = False
        try:
            H._run_storyboard_phase("script")
        except H.HyperFramesBrollError:
            raised = True
    _assert(raised, "raise при отсутствии storyboard.json", errors)


# ── 5. build-промпт получает storyboard ──────────────────────────────────
def test_build_prompt_uses_storyboard(errors):
    print("\n-- _build_prompt(storyboard=...) встраивает архетипы --")
    sb = _golden_storyboard()
    p = H._build_prompt("СЦЕНАРИЙ", storyboard=sb)
    _assert("reserve_gauge" in p or "hero_number" in p, "архетипы из storyboard в промпте", errors)
    _assert("storyboard" in p.lower(), "промпт ссылается на storyboard", errors)


# ── 6. _revert_stray сохраняет storyboard.json ───────────────────────────
def test_revert_keeps_storyboard(errors):
    print("\n-- storyboard.json НЕ откатывается _revert_stray --")
    # проверяем по списку «разрешённых» (whitelist) — storyboard в нём
    _assert(hasattr(H, "STORYBOARD_FILE"), "константа STORYBOARD_FILE есть", errors)
    if hasattr(H, "STORYBOARD_FILE"):
        # эмулируем git diff с storyboard.json среди изменённых
        captured = {}

        class _FakeProc:
            def __init__(self, out): self.stdout = out
        def _fake_git(args):
            captured.setdefault("calls", []).append(args)
            if args[:2] == ["diff", "--name-only"]:
                return _FakeProc(f"{H.STORYBOARD_FILE}\nscene_01.html\nstray_file.txt\n")
            return _FakeProc("")
        with patch.object(H, "_git", side_effect=_fake_git), \
             patch.object(H.Path, "exists", return_value=True):
            H._revert_stray()
        # ищем вызов checkout — storyboard.json и scene_01 НЕ должны быть в нём, stray_file должен
        checkout_calls = [c for c in captured.get("calls", []) if c and c[0] == "checkout"]
        reverted = set()
        for c in checkout_calls:
            reverted.update(c[2:])  # ["checkout","--",*files]
        _assert(H.STORYBOARD_FILE not in reverted, f"storyboard.json НЕ откачен (reverted={reverted})", errors)
        _assert("stray_file.txt" in reverted, "посторонний файл откачен", errors)


# ── 7. generate_hyperframes_broll: фаза 1 ДО фазы 2 ──────────────────────
def test_generate_calls_storyboard_before_build(errors):
    print("\n-- generate: storyboard-фаза вызывается ДО build-фазы --")
    order = []
    sb = _golden_storyboard()
    # build теперь = _run_build_phase (per-scene), не монолитный _run_claude
    with patch.object(H, "ensure_git_baseline"), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_run_storyboard_phase", side_effect=lambda s: (order.append("storyboard"), (sb, 0.0))[1]), \
         patch.object(H, "_run_build_phase", side_effect=lambda s: order.append("build") or 0.0), \
         patch.object(H, "_inspect_all_scenes", return_value={}), \
         patch.object(H, "_render_all", return_value=([Path("a.mp4")], [])), \
         patch.object(H, "HF_PROJECT", Path("/tmp")):
        H.generate_hyperframes_broll("script", "/tmp/_tp_test")
    _assert(order and order[0] == "storyboard", f"storyboard первым (order={order})", errors)
    _assert("build" in order and order.index("storyboard") < order.index("build"),
            "build после storyboard", errors)


def main():
    print("=" * 60)
    print("test_hyperframes_two_phase")
    print("=" * 60)
    errors = []
    test_storyboard_prompt(errors)
    test_storyboard_fix_prompt(errors)
    test_read_storyboard_exists(errors)
    if hasattr(H, "_run_storyboard_phase"):
        test_phase_valid_first(errors)
        test_phase_invalid_then_valid(errors)
        test_phase_always_invalid_raises(errors)
        test_phase_missing_file_raises(errors)
    else:
        _assert(False, "_run_storyboard_phase есть", errors)
    test_build_prompt_uses_storyboard(errors)
    test_revert_keeps_storyboard(errors)
    test_generate_calls_storyboard_before_build(errors)
    print()
    if errors:
        print(f"FAIL: {len(errors)} assertion(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
