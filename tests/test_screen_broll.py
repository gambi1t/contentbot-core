"""TDD: screen_broll.py — оркестратор экранов кода/UI (Path B).

Тема → Claude отдаёт JSON-пропсы → рендер готовой Remotion-композиции с --props.
Тесты изолируют I/O-границы (_run_claude_json, _render_screen, subprocess) моками;
реальный рендер проверяется отдельно ручным E2E.

Запуск: python -m pytest tests/test_screen_broll.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import screen_broll as sb  # noqa: E402


# ── _extract_json ────────────────────────────────────────────────────
def test_extract_json_plain():
    assert sb._extract_json('{"a": 1}')["a"] == 1


def test_extract_json_fenced():
    assert sb._extract_json('```json\n{"a": 2}\n```')["a"] == 2


def test_extract_json_embedded_in_prose():
    assert sb._extract_json('вот пропсы: {"a": 3}. готово')["a"] == 3


def test_extract_json_invalid_raises():
    with pytest.raises(sb.ScreenBrollError):
        sb._extract_json("здесь нет json")


# ── _validate_props ──────────────────────────────────────────────────
def test_validate_fills_defaults_and_clamps():
    p = sb._validate_props(
        {
            "toolName": "CLAUDE CODE",
            "promptText": "build mvp",
            "outputLines": ["a", "b", "c", "d", "e"],  # >4 → обрезать
            "toolNameStyle": "weird",                  # не из набора → block
        },
        "AiToolDeepDive",
    )
    assert p["toolNameStyle"] == "block"
    assert len(p["outputLines"]) == 4
    assert p["promptPrefix"] == ">"     # дефолт проставлен
    assert "tagBadge" in p and "outroLine" in p


def test_validate_missing_required_raises():
    with pytest.raises(sb.ScreenBrollError):
        sb._validate_props({"promptText": "y"}, "AiToolDeepDive")  # нет toolName


def test_validate_non_dict_raises():
    with pytest.raises(sb.ScreenBrollError):
        sb._validate_props(["not", "a", "dict"], "AiToolDeepDive")


# ── generate_screen_broll (моки I/O) ─────────────────────────────────
def test_generate_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "BROLL_PROJECT", tmp_path)
    sample = {
        "toolName": "CLAUDE CODE", "promptText": "build mvp",
        "outputLines": ["✓ created app"], "toolNameStyle": "block",
    }
    monkeypatch.setattr(sb, "_run_claude_json", lambda prompt: (dict(sample), 0.012))

    calls: dict = {}

    def fake_render(comp_id, props_path, out_path):
        calls["comp_id"] = comp_id
        calls["props_path"] = Path(props_path)
        Path(out_path).write_bytes(b"\x00\x00")  # имитируем готовый mp4
        return True, ""

    monkeypatch.setattr(sb, "_render_screen", fake_render)

    clip, cost = sb.generate_screen_broll(
        "Claude Code пишет MVP", tmp_path / "proj", template="AiToolDeepDive"
    )
    assert clip.exists()
    assert cost == 0.012
    assert calls["comp_id"] == "AiToolDeepDive-ClaudeCode"
    # props.json записан и содержит контент + проставленный дефолт
    props = json.loads(calls["props_path"].read_text(encoding="utf-8"))
    assert props["toolName"] == "CLAUDE CODE"
    assert props["promptPrefix"] == ">"


def test_generate_render_fail_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "BROLL_PROJECT", tmp_path)
    monkeypatch.setattr(
        sb, "_run_claude_json",
        lambda prompt: ({"toolName": "X", "promptText": "y"}, 0.0),
    )
    monkeypatch.setattr(sb, "_render_screen", lambda *a: (False, "boom"))
    with pytest.raises(sb.ScreenBrollError):
        sb.generate_screen_broll("тема", tmp_path / "proj")


def test_generate_unknown_template_raises(tmp_path):
    with pytest.raises(sb.ScreenBrollError):
        sb.generate_screen_broll("тема", tmp_path, template="NoSuchTemplate")


def test_generate_rejects_bad_comp_id(tmp_path, monkeypatch):
    """comp_id-гейт: path-traversal / подмена на опцию отклоняются до рендера."""
    monkeypatch.setattr(sb, "BROLL_PROJECT", tmp_path)
    for bad in ["../evil", "-config", "a/b", "C:\\x", "comp;rm"]:
        with pytest.raises(sb.ScreenBrollError):
            sb.generate_screen_broll("тема", tmp_path / "proj", comp_id=bad)


# ── _render_screen — форма команды ───────────────────────────────────
def test_render_cmd_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "BROLL_PROJECT", tmp_path)
    captured: dict = {}

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    props = tmp_path / "p.json"; props.write_text("{}", encoding="utf-8")
    out = tmp_path / "o.mp4"; out.write_bytes(b"x")  # exists() → True после run

    ok, err = sb._render_screen("AiToolDeepDive-ClaudeCode", props, out)
    assert ok, err
    cmd = captured["cmd"]
    assert "npx" in cmd and "remotion" in cmd and "render" in cmd
    assert "AiToolDeepDive-ClaudeCode" in cmd
    assert any(str(x).startswith("--props=") for x in cmd)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
