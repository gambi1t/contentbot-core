"""TDD: Фаза 1 hardening per-scene build (3 июня, синтез субагент+ChatGPT+GitHub).

Корень медленноты (подтверждён GitHub heygen-com/hyperframes): официальный
SKILL.md спроектирован для ИНТЕРАКТИВА — обязательный self-loop
lint→validate→inspect (npx, на cold cache минуты) + чтение 5+ references. В
headless `claude -p` Claude добросовестно это выполняет → 10+ мин/сцена.

Фаза 1 (дёшево, измеримо):
  1. `_parse_stream(stdout)` — вынесен парсинг (для timeout-capture).
  2. `_run_claude` при таймауте ПАРСИТ e.stdout (закрыть слепое пятно), не
     выбрасывает молча.
  3. `_run_claude(tools=..., max_turns=N)` — параметры в команде.
  4. build-промпт БЕЗ чтения SKILL.md (триггерит весь loop), inline brand.
  5. `_scene_valid_minimal` — строгая проверка (не >200 байт).

Run: python tests/test_hyperframes_hardening_phase1.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))
import hyperframes_broll as H  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


_STREAM = (
    '{"type":"system","subtype":"init"}\n'
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read"}]}}\n'
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}\n'
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}\n'
    '{"type":"assistant","message":{"content":[{"type":"text","text":"thinking"}]}}\n'
)


# ── 1. _parse_stream ─────────────────────────────────────────────────────
def test_parse_stream(errors):
    print("\n-- _parse_stream извлекает tool_counts + last event --")
    _assert(hasattr(H, "_parse_stream"), "_parse_stream есть", errors)
    if not hasattr(H, "_parse_stream"):
        return
    d = H._parse_stream(_STREAM)
    _assert(d.get("tool_counts", {}).get("Bash") == 2, f"Bash=2 (got {d.get('tool_counts')})", errors)
    _assert(d.get("tool_counts", {}).get("Read") == 1, "Read=1", errors)
    _assert(d.get("num_events", 0) == 5, f"5 событий (got {d.get('num_events')})", errors)


# ── 2. timeout-capture (слепое пятно) ────────────────────────────────────
def test_timeout_captures_stdout(errors):
    print("\n-- при таймауте _run_claude парсит e.stdout (видимость) --")
    captured = {}

    def _fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"), output=_STREAM, stderr="")

    # перехватываем _parse_stream чтобы убедиться, что timeout-ветка его зовёт
    orig = H._parse_stream if hasattr(H, "_parse_stream") else None
    def _spy_parse(s):
        captured["called"] = True
        captured["stdout"] = s
        return orig(s) if orig else {}

    with patch.object(H.subprocess, "run", side_effect=_fake_run), \
         patch.object(H, "_parse_stream", side_effect=_spy_parse):
        raised = False
        try:
            H._run_claude("p", timeout=5)
        except H.HyperFramesTimeout:
            raised = True
    _assert(raised, "HyperFramesTimeout поднят", errors)
    _assert(captured.get("called"), "timeout-ветка распарсила stdout (не выбросила)", errors)
    _assert(captured.get("stdout") == _STREAM, "распарсен именно partial stdout таймаута", errors)


# ── 3. tools / max_turns параметры ───────────────────────────────────────
def test_tools_and_maxturns_params(errors):
    print("\n-- _run_claude(tools=..., max_turns=N) → в команде --")
    cap = {}
    def _fake_run(cmd, **kw):
        cap["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout='{"type":"result","total_cost_usd":0.1}', stderr="")
    with patch.object(H.subprocess, "run", side_effect=_fake_run):
        H._run_claude("p", tools="Read,Write", max_turns=6)
    cmd_str = " ".join(map(str, cap.get("cmd", [])))
    _assert("Read,Write" in cmd_str, "allowedTools=Read,Write (без Bash)", errors)
    _assert("--max-turns" in cmd_str and "6" in cmd_str, "--max-turns 6 в команде", errors)


# ── 4. build-промпт без SKILL.md, inline brand ───────────────────────────
def test_scene_prompt_no_skill_inline_brand(errors):
    print("\n-- _build_scene_prompt: НЕ читать SKILL.md, brand inline --")
    sb = {"scenes": [{"id": "scene_01", "business_archetype": "hero_number",
                      "hf_technique": "counter_animation", "visual_style": "swiss_pulse",
                      "motion_family": "counter_build", "density": "balanced",
                      "scale_profile": "hero", "primary_text": "X",
                      "script_beat": "beat", "reason": "r"}]}
    p = H._build_scene_prompt(sb, "scene_01", [])
    _assert("SKILL.md" not in p, "НЕ просит читать SKILL.md (триггер lint-loop)", errors)
    _assert("reference_pack" in p.lower(), "reference_pack оставлен (наша выжимка)", errors)
    _assert("#FF5722" in p or "ff5722" in p.lower(), "accent #FF5722 inline (без чтения design.md)", errors)
    _assert("@font-face" in p or "index.html" in p, "указание про шрифты (index.html/font-face)", errors)
    # явный запрет на CLI-проверки
    low = p.lower()
    _assert("lint" in low or "не запускай" in low, "явный запрет lint/CLI-проверок", errors)


# ── 5. _scene_valid_minimal ──────────────────────────────────────────────
def test_scene_valid_minimal(errors):
    print("\n-- _scene_valid_minimal: строгая проверка --")
    _assert(hasattr(H, "_scene_valid_minimal"), "_scene_valid_minimal есть", errors)
    if not hasattr(H, "_scene_valid_minimal"):
        return
    import tempfile
    d = Path(tempfile.mkdtemp())
    good = ('<!doctype html><html><body>'
            '<div data-composition-id="scene_01" data-duration="5" data-width="1080" data-height="1920"></div>'
            '<script>window.__timelines={};const tl=gsap.timeline({paused:true});</script></html>'
            + "x" * 5000)
    (d / "good.html").write_text(good, encoding="utf-8")
    ok, issues = H._scene_valid_minimal(d / "good.html", "scene_01")
    _assert(ok, f"валидный html проходит (issues={issues})", errors)

    # битый: маленький, без timelines
    (d / "bad.html").write_text("<html>tiny</html>", encoding="utf-8")
    ok2, issues2 = H._scene_valid_minimal(d / "bad.html", "scene_01")
    _assert(not ok2, "битый/маленький html не проходит", errors)

    # недетерминизм
    (d / "rnd.html").write_text(good.replace("gsap.timeline", "Math.random();gsap.timeline"), encoding="utf-8")
    ok3, issues3 = H._scene_valid_minimal(d / "rnd.html", "scene_01")
    _assert(not ok3 and any("random" in i.lower() or "determin" in i.lower() for i in issues3),
            f"Math.random ловится (issues={issues3})", errors)


def main():
    print("=" * 60)
    print("test_hyperframes_hardening_phase1")
    print("=" * 60)
    errors = []
    test_parse_stream(errors)
    test_timeout_captures_stdout(errors)
    test_tools_and_maxturns_params(errors)
    test_scene_prompt_no_skill_inline_brand(errors)
    test_scene_valid_minimal(errors)
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
