"""TDD: 1 июня — корневой фикс HyperFrames-таймаута + stream-json heartbeat.

Контекст:
  • 29 мая Claude уложился за ~4 минуты на 6 сцен. 31 мая упал в
    таймаут 900s на ТОТ ЖЕ setup. Корень нестабилен. Пока ищем —
    страховка: поднять CLAUDE_TIMEOUT до 1800 (30 мин).
  • Сейчас в коде `--output-format json` — субпроцесс пишет в stdout
    ТОЛЬКО когда завершён. Эти 15 минут — blackbox. Переходим на
    `--output-format stream-json --verbose` чтобы можно было видеть
    прогресс (читая stdout построчно).

Контракт:
  (1) CLAUDE_TIMEOUT == 1800 (страховка, 30 мин)
  (2) команда claude в _run_claude содержит `--output-format` со
      значением `stream-json` (а не `json`)
  (3) команда содержит `--verbose` (без него stream-json не выдаёт
      все события)
  (4) stream НЕ ломает парсинг cost_usd — мы должны уметь читать
      финальное событие `type=result` где есть `total_cost_usd`

Run: python tests/test_hyperframes_timeout_and_stream.py
"""
from __future__ import annotations

import os
import re
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

import hyperframes_broll  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_timeout_raised_to_1800(errors: list[str]) -> None:
    print("\n-- CLAUDE_TIMEOUT поднят до 1800 (30 мин) --")
    _assert(
        hyperframes_broll.CLAUDE_TIMEOUT == 1800,
        f"CLAUDE_TIMEOUT == 1800 (got {hyperframes_broll.CLAUDE_TIMEOUT})",
        errors,
    )


def test_command_uses_stream_json(errors: list[str]) -> None:
    print("\n-- _run_claude передаёт --output-format stream-json --verbose --")
    captured = {}

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # успешный stream-json: 3 строки JSONL — init, result
        captured["stdout"] = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n'
            '{"type":"result","subtype":"success","total_cost_usd":0.1234,"num_turns":3,"result":"done"}\n'
        )
        return SimpleNamespace(returncode=0, stdout=captured["stdout"], stderr="")

    with patch.object(hyperframes_broll.subprocess, "run", side_effect=_fake_run):
        try:
            cost = hyperframes_broll._run_claude("test prompt")
        except Exception as e:
            _assert(False, f"исключение при stream-json парсинге: {e}", errors)
            return

    cmd = captured.get("cmd", [])
    cmd_str = " ".join(map(str, cmd))
    _assert("--output-format" in cmd_str, "флаг --output-format есть", errors)
    # выделим значение --output-format
    m = re.search(r"--output-format\s+(\S+)", cmd_str)
    if m:
        val = m.group(1)
        _assert(
            val == "stream-json",
            f"--output-format == stream-json (got {val!r})",
            errors,
        )
    _assert("--verbose" in cmd_str, "флаг --verbose есть (без него stream-json неполный)", errors)
    _assert(
        abs(cost - 0.1234) < 1e-6,
        f"cost_usd распарсен из финального type=result (got {cost})",
        errors,
    )


def test_stream_with_bad_lines_does_not_crash(errors: list[str]) -> None:
    print("\n-- stream с мусорной строкой не валит парсинг --")

    def _fake_run(cmd, **kw):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                'not-json garbage line\n'
                '{"type":"system","subtype":"init"}\n'
                '\n'
                '{"type":"result","subtype":"success","total_cost_usd":0.5,"num_turns":2,"result":"x"}\n'
            ),
            stderr="",
        )

    with patch.object(hyperframes_broll.subprocess, "run", side_effect=_fake_run):
        try:
            cost = hyperframes_broll._run_claude("test")
            _assert(abs(cost - 0.5) < 1e-6, f"cost == 0.5 (got {cost})", errors)
        except Exception as e:
            _assert(False, f"парсинг упал на мусоре: {e}", errors)


# ── MEDIUM-A: safety от malformed JSON-структуры (агент-ревью 1 июня) ────

def _run_with_stdout(stdout: str) -> float | Exception:
    def _fake_run(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    with patch.object(hyperframes_broll.subprocess, "run", side_effect=_fake_run):
        try:
            return hyperframes_broll._run_claude("test")
        except Exception as e:
            return e


def test_assistant_message_null(errors: list[str]) -> None:
    print("\n-- safety: assistant с message=null (ключ есть, значение None) --")
    # Реальный риск: evt.get("message", {}) возвращает None (ключ есть со
    # значением None), потом .get() кидает AttributeError. Это пройдёт ПОСЛЕ
    # успешной 15-мин генерации → юзер видит ошибку при готовых сценах.
    stdout = (
        '{"type":"system","subtype":"init"}\n'
        '{"type":"assistant","message":null}\n'
        '{"type":"result","subtype":"success","total_cost_usd":0.7,"num_turns":2,"result":"ok"}\n'
    )
    cost = _run_with_stdout(stdout)
    _assert(not isinstance(cost, Exception), f"не падает на message=null (got {type(cost).__name__ if isinstance(cost, Exception) else 'ok'}: {cost})", errors)
    if not isinstance(cost, Exception):
        _assert(abs(cost - 0.7) < 1e-6, f"cost из result, не из ошибочного assistant (got {cost})", errors)


def test_assistant_content_not_list(errors: list[str]) -> None:
    print("\n-- safety: message.content не list, или blk не dict --")
    stdout = (
        '{"type":"assistant","message":{"content":"строка вместо списка"}}\n'
        '{"type":"assistant","message":{"content":[null,"строка",{"type":"text"}]}}\n'
        '{"type":"result","subtype":"success","total_cost_usd":0.3,"num_turns":1,"result":"x"}\n'
    )
    cost = _run_with_stdout(stdout)
    _assert(not isinstance(cost, Exception), f"не падает на нестандартном content (got {type(cost).__name__ if isinstance(cost, Exception) else 'ok'})", errors)


def test_total_cost_usd_non_numeric(errors: list[str]) -> None:
    print("\n-- safety: total_cost_usd = 'N/A' (нечисловая строка) --")
    stdout = '{"type":"result","subtype":"success","total_cost_usd":"N/A","num_turns":1,"result":"ok"}\n'
    cost = _run_with_stdout(stdout)
    _assert(not isinstance(cost, Exception), f"не падает на нечисловом cost (got {type(cost).__name__ if isinstance(cost, Exception) else 'ok'})", errors)
    if not isinstance(cost, Exception):
        _assert(cost == 0.0, f"fallback cost=0.0 (got {cost})", errors)


def test_total_cost_usd_numeric_string(errors: list[str]) -> None:
    print("\n-- safety: total_cost_usd = '0.42' (числовая строка) --")
    stdout = '{"type":"result","subtype":"success","total_cost_usd":"0.42","num_turns":1,"result":"ok"}\n'
    cost = _run_with_stdout(stdout)
    _assert(not isinstance(cost, Exception), "не падает на числовой строке", errors)
    if not isinstance(cost, Exception):
        _assert(abs(cost - 0.42) < 1e-6, f"числовая строка распарсена (got {cost})", errors)


def main() -> int:
    print("=" * 60)
    print("test_hyperframes_timeout_and_stream")
    print("=" * 60)
    errors: list[str] = []
    test_timeout_raised_to_1800(errors)
    test_command_uses_stream_json(errors)
    test_stream_with_bad_lines_does_not_crash(errors)
    test_assistant_message_null(errors)
    test_assistant_content_not_list(errors)
    test_total_cost_usd_non_numeric(errors)
    test_total_cost_usd_numeric_string(errors)
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
