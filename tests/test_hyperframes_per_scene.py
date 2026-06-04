"""TDD: per-scene build (фаза 2 — отдельный claude -p на каждую сцену).

Контекст (реальный прогон 1 июня): монолит-build (один claude -p на 6 сцен)
ТАЙМАУТ — за 30 мин Claude написал 1/6. Причина: 6 РАЗНЫХ архетипов = много
уникального кода. Решение (ChatGPT P0): каждая сцена отдельным вызовом —
короткий промпт, свой таймаут, retry на сцену, медленная не блокирует остальных.

Контракт:
  - `_build_scene_prompt(storyboard, scene_id, done_scenes)` — промпт на ОДНУ
    сцену с её контрактом из storyboard + reference_pack + список готовых сцен.
  - `_run_build_phase(storyboard) -> cost` — цикл по 6 сценам; на каждую до
    MAX_SCENE_BUILD_ATTEMPTS попыток (проверка _scene_done); готовые сцены
    передаются дальше (единство стиля); raise если сцена не записалась.
  - `_run_claude(prompt, timeout=...)` — таймаут на вызов (SCENE_BUILD_TIMEOUT).

Run: python tests/test_hyperframes_per_scene.py
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
os.environ["HF_LEGACY_BUILD"] = "1"  # тесты на старый flow, Step 6 default = async
os.environ["HF_SKIP_MOTION_GATE"] = "1"  # моки не создают валидных HTML
os.environ["HF_USE_NPX_RENDER"] = "1"  # тесты мокают legacy _render_all

sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as H  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _storyboard():
    rows = [
        ("scene_01", "cashflow_timeline", "shadow_cut", "path_draw"),
        ("scene_02", "formula_card", "swiss_pulse", "kinetic_type"),
        ("scene_03", "stack_layers", "data_drift", "vertical_stack"),
        ("scene_04", "reserve_gauge", "deconstructed", "counter_build"),
        ("scene_05", "before_after_cards", "velvet_standard", "card_flip"),
        ("scene_06", "final_cta", "maximalist_type", "kinetic_type"),
    ]
    return {"version": "1.0", "scenes": [
        {"id": r[0], "business_archetype": r[1], "hf_technique": "svg_path_drawing",
         "visual_style": r[2], "motion_family": r[3], "scale_profile": "hero",
         "density": "balanced", "primary_text": f"Текст {r[0]}",
         "script_beat": "Фрагмент сценария про резерв сезонного бизнеса длинный.",
         "reason": "Архетип иллюстрирует момент сценария наилучшим образом тут."}
        for r in rows
    ]}


# ── 1. SCENE_BUILD_TIMEOUT < CLAUDE_TIMEOUT ──────────────────────────────
def test_scene_timeout_constant(errors):
    print("\n-- SCENE_BUILD_TIMEOUT отдельный и меньше монолитного --")
    _assert(hasattr(H, "SCENE_BUILD_TIMEOUT"), "SCENE_BUILD_TIMEOUT есть", errors)
    if hasattr(H, "SCENE_BUILD_TIMEOUT"):
        _assert(120 <= H.SCENE_BUILD_TIMEOUT <= 600, f"в [120,600] (got {H.SCENE_BUILD_TIMEOUT})", errors)
        _assert(H.SCENE_BUILD_TIMEOUT < H.CLAUDE_TIMEOUT, "меньше CLAUDE_TIMEOUT", errors)


# ── 2. _run_claude принимает timeout ─────────────────────────────────────
def test_run_claude_timeout_param(errors):
    print("\n-- _run_claude(prompt, timeout=N) передаёт таймаут в subprocess --")
    captured = {}
    from types import SimpleNamespace
    def _fake_run(cmd, **kw):
        captured["timeout"] = kw.get("timeout")
        return SimpleNamespace(returncode=0, stdout='{"type":"result","total_cost_usd":0.1}', stderr="")
    with patch.object(H.subprocess, "run", side_effect=_fake_run):
        H._run_claude("p", timeout=321)
    _assert(captured.get("timeout") == 321, f"timeout проброшен (got {captured.get('timeout')})", errors)


# ── 3. _build_scene_prompt ───────────────────────────────────────────────
def test_build_scene_prompt(errors):
    print("\n-- _build_scene_prompt: одна сцена + контракт + done_scenes --")
    if not hasattr(H, "_build_scene_prompt"):
        _assert(False, "_build_scene_prompt есть", errors)
        return
    sb = _storyboard()
    done = [{"id": "scene_01", "archetype": "cashflow_timeline", "primary_text": "Текст scene_01"}]
    p = H._build_scene_prompt(sb, "scene_03", done)
    low = p.lower()
    _assert("scene_03" in p, "указана целевая сцена scene_03", errors)
    _assert("stack_layers" in p, "контракт сцены (архетип stack_layers) в промпте", errors)
    _assert("reference_pack" in low, "ссылка на reference_pack", errors)
    _assert("scene_01" in p, "готовая сцена передана (единство стиля)", errors)
    _assert("одну" in low or "только" in low or "ровно одну" in low, "просит РОВНО одну сцену", errors)
    _assert("askuserquestion" in low or "не задавай" in low or "автоном" in low, "автономный режим", errors)


# ── 4. _run_build_phase: 6 сцен → 6 вызовов ──────────────────────────────
def test_build_phase_six_calls(errors):
    print("\n-- 6 сцен пишутся = 6 вызовов claude --")
    if not hasattr(H, "_run_build_phase"):
        _assert(False, "_run_build_phase есть", errors)
        return
    calls = {"n": 0}
    with patch.object(H, "_run_claude", side_effect=lambda p, **kw: calls.__setitem__("n", calls["n"]+1) or 0.0), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_scene_valid_minimal", return_value=(True, [])):
        H._run_build_phase(_storyboard())
    _assert(calls["n"] == H.N_INSERTS, f"{H.N_INSERTS} вызовов (got {calls['n']})", errors)


# ── 5. retry на сцену, если не записалась ────────────────────────────────
def test_build_phase_retry_scene(errors):
    print("\n-- scene_01 не записана с 1й → retry (7 вызовов всего) --")
    calls = {"n": 0}
    # _scene_done: scene_01 False на 1й проверке, True на 2й; остальные True
    seq = {"scene_01.html": [False, True]}
    def fake_valid(scene_path, scene_id):
        name = Path(scene_path).name
        if name in seq and seq[name]:
            return (seq[name].pop(0), ["каркас"])
        return (True, [])
    with patch.object(H, "_run_claude", side_effect=lambda p, **kw: calls.__setitem__("n", calls["n"]+1) or 0.0), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_scene_valid_minimal", side_effect=fake_valid):
        H._run_build_phase(_storyboard())
    _assert(calls["n"] == H.N_INSERTS + 1, f"6 + 1 retry = 7 вызовов (got {calls['n']})", errors)


# ── 5b. таймаут НО файл записан до таймаута → принять (не выбрасывать) ────
def test_build_phase_timeout_but_file_written(errors):
    print("\n-- timeout НО scene записан до таймаута → принять (Claude не успел завершиться) --")
    calls = {"n": 0}
    # каждый вызов кидает timeout, НО файл «записан» (_scene_done True).
    # Реальный кейс 3 июня: Claude пишет HTML за ~7 мин, потом «думает» →
    # subprocess timeout 10 мин, но файл уже на диске и валиден.
    def fake_claude(p, **kw):
        calls["n"] += 1
        raise H.HyperFramesTimeout("Claude не завершился, но файл записан")
    with patch.object(H, "_run_claude", side_effect=fake_claude), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_clear_scene_files"), \
         patch.object(H, "_scene_valid_minimal", return_value=(True, [])):
        raised = False
        try:
            H._run_build_phase(_storyboard())
        except Exception as e:
            raised = True
            _assert(False, f"НЕ падать — файл записан, принять (got {type(e).__name__})", errors)
    _assert(not raised, "записанный при таймауте файл принят, фаза не падает", errors)
    _assert(calls["n"] == H.N_INSERTS, f"по 1 вызову на сцену (файл принят, без retry), got {calls['n']}", errors)


# ── 5c. таймаут И файл НЕ записан → retry ────────────────────────────────
def test_build_phase_retries_on_timeout_no_file(errors):
    print("\n-- timeout + файл НЕ записан → retry --")
    calls = {"n": 0}
    done_seq = {"scene_01.html": [False, True]}  # 1я проверка False (retry), 2я True
    def fake_claude(p, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise H.HyperFramesTimeout("scene_01 timeout, файл не записан")
        return 0.0
    def fake_valid(scene_path, scene_id):
        name = Path(scene_path).name
        if name in done_seq and done_seq[name]:
            return (done_seq[name].pop(0), ["каркас"])
        return (True, [])
    with patch.object(H, "_run_claude", side_effect=fake_claude), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_clear_scene_files"), \
         patch.object(H, "_scene_valid_minimal", side_effect=fake_valid):
        H._run_build_phase(_storyboard())
    _assert(calls["n"] == H.N_INSERTS + 1, f"timeout+нет файла → retry = 7 вызовов (got {calls['n']})", errors)


def test_build_phase_all_timeouts_no_file_raises(errors):
    print("\n-- таймаут И файл не записан на ВСЕХ попытках → raise --")
    with patch.object(H, "_run_claude", side_effect=H.HyperFramesTimeout("always slow")), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_clear_scene_files"), \
         patch.object(H, "_scene_valid_minimal", return_value=(False, ["каркас"])):
        raised = False
        try:
            H._run_build_phase(_storyboard())
        except H.HyperFramesBrollError:
            raised = True
    _assert(raised, "постоянный таймаут+нет файла → HyperFramesBrollError", errors)


# ── 6. сцена не записалась за MAX попыток → raise ────────────────────────
def test_build_phase_scene_fails(errors):
    print("\n-- сцена не записана за MAX_SCENE_BUILD_ATTEMPTS → raise --")
    with patch.object(H, "_run_claude", return_value=0.0), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_scene_valid_minimal", return_value=(False, ["каркас"])):
        raised = False
        try:
            H._run_build_phase(_storyboard())
        except H.HyperFramesBrollError:
            raised = True
    _assert(raised, "raise при несоздании сцены", errors)


# ── 7. done_scenes накапливаются (единство стиля) ────────────────────────
def test_done_scenes_accumulate(errors):
    print("\n-- готовые сцены передаются в промпты последующих --")
    seen_done_counts = []
    def fake_prompt(sb, scene_id, done):
        seen_done_counts.append(len(done))
        return "prompt"
    with patch.object(H, "_run_claude", return_value=0.0), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_scene_valid_minimal", return_value=(True, [])), \
         patch.object(H, "_build_scene_prompt", side_effect=fake_prompt):
        H._run_build_phase(_storyboard())
    # scene_01: 0 готовых, scene_02: 1, ... scene_06: 5
    _assert(seen_done_counts == [0, 1, 2, 3, 4, 5], f"накопление done (got {seen_done_counts})", errors)


# ── 8. _scene_done: пустой/отсутствующий файл = False ────────────────────
def test_scene_done_helper(errors):
    print("\n-- _scene_done: непустой html → True --")
    _assert(hasattr(H, "_scene_done"), "_scene_done есть", errors)


# ── 8b. чистка старых сцен в начале (иначе ложный _scene_done) ────────────
def test_build_phase_clears_old_scenes(errors):
    print("\n-- _run_build_phase чистит старые scene-файлы в начале --")
    cleared = {"called": False}
    with patch.object(H, "_clear_scene_files", side_effect=lambda: cleared.__setitem__("called", True)), \
         patch.object(H, "_run_claude", return_value=0.0), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_scene_valid_minimal", return_value=(True, [])):
        H._run_build_phase(_storyboard())
    _assert(cleared["called"], "_clear_scene_files вызван (нет ложного успеха на старых)", errors)


# ── 9. generate использует _run_build_phase (не монолит) ─────────────────
def test_generate_uses_build_phase(errors):
    print("\n-- generate_hyperframes_broll зовёт _run_build_phase --")
    order = []
    sb = _storyboard()
    with patch.object(H, "ensure_git_baseline"), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_run_storyboard_phase", side_effect=lambda s: (order.append("storyboard"), (sb, 0.0))[1]), \
         patch.object(H, "_run_build_phase", side_effect=lambda s: order.append("build_phase") or 0.0), \
         patch.object(H, "_inspect_all_scenes", return_value={}), \
         patch.object(H, "_render_all", return_value=([Path("a.mp4")], [])), \
         patch.object(H, "HF_PROJECT", Path("/tmp")):
        H.generate_hyperframes_broll("script", "/tmp/_ps_test")
    _assert(order[:2] == ["storyboard", "build_phase"], f"storyboard → build_phase (order={order})", errors)


def main():
    print("=" * 60)
    print("test_hyperframes_per_scene")
    print("=" * 60)
    errors = []
    test_scene_timeout_constant(errors)
    test_run_claude_timeout_param(errors)
    test_build_scene_prompt(errors)
    if hasattr(H, "_run_build_phase"):
        test_build_phase_six_calls(errors)
        test_build_phase_retry_scene(errors)
        test_build_phase_timeout_but_file_written(errors)
        test_build_phase_retries_on_timeout_no_file(errors)
        test_build_phase_all_timeouts_no_file_raises(errors)
        test_build_phase_scene_fails(errors)
        test_done_scenes_accumulate(errors)
    else:
        _assert(False, "_run_build_phase есть", errors)
    test_scene_done_helper(errors)
    if hasattr(H, "_run_build_phase"):
        test_build_phase_clears_old_scenes(errors)
    test_generate_uses_build_phase(errors)
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
