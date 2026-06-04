"""TDD: _run_claude должен ловить subprocess.TimeoutExpired и преобразовывать
в HyperFramesTimeout с осмысленным текстом — не пробрасывать сырой
TimeoutExpired, чей str() содержит ВСЮ команду с промптом.

Баг (Артём 31 мая, скрин в 22:08 МСК):
  • subprocess.TimeoutExpired после CLAUDE_TIMEOUT=900s — Claude Code не
    уложился в 15 минут.
  • Exception пролетел мимо моей классификации по proc.returncode (proc
    вообще не существует при таймауте).
  • В bot.py `except Exception as e:` → `await edit_message_text(... {e})`
    показал юзеру весь промпт сырым в Telegram (~3500 символов, режется
    на пол-сообщения).

Контракт фикса:
  • subprocess.TimeoutExpired → HyperFramesTimeout (наследник
    HyperFramesBrollError) с коротким текстом без промпта
  • НЕ ретраим — таймаут не transient, второй раз тоже не уложится
  • bot.py показывает «не уложился за N минут» + кнопки повтора/Remotion
  • для общего Exception — обрезка str(e) до 200 симв (защита от утечки)

Run: python tests/test_hyperframes_timeout.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
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


_LONG_PROMPT = "Очень длинный промпт " * 200  # ~4000 символов


def _raise_timeout(*a, **kw):
    raise subprocess.TimeoutExpired(cmd=["claude", "-p", _LONG_PROMPT], timeout=900)


def _run_claude_with_timeout():
    import time as _time_mod
    with patch.object(hyperframes_broll.subprocess, "run", side_effect=_raise_timeout), \
         patch.object(_time_mod, "sleep", lambda *_: None):
        try:
            hyperframes_broll._run_claude(_LONG_PROMPT)
        except Exception as e:
            return e
    return None


def test_timeout_class_exists(errors: list[str]) -> None:
    print("\n-- HyperFramesTimeout exists --")
    cls = getattr(hyperframes_broll, "HyperFramesTimeout", None)
    _assert(cls is not None, "класс HyperFramesTimeout определён", errors)
    if cls:
        _assert(
            issubclass(cls, hyperframes_broll.HyperFramesBrollError),
            "наследник HyperFramesBrollError",
            errors,
        )


def test_timeout_raises_hf_timeout(errors: list[str]) -> None:
    print("\n-- TimeoutExpired → HyperFramesTimeout (НЕ сырой TimeoutExpired) --")
    exc = _run_claude_with_timeout()
    _assert(exc is not None, "exception поднят", errors)
    if exc:
        _assert(
            isinstance(exc, getattr(hyperframes_broll, "HyperFramesTimeout", type(None))),
            f"тип = HyperFramesTimeout (got {type(exc).__name__})",
            errors,
        )
        _assert(
            not isinstance(exc, subprocess.TimeoutExpired),
            "exception НЕ сырой subprocess.TimeoutExpired",
            errors,
        )


def test_timeout_message_does_not_leak_prompt(errors: list[str]) -> None:
    print("\n-- сообщение НЕ содержит сырого промпта (короткое) --")
    exc = _run_claude_with_timeout()
    if exc:
        msg = str(exc)
        _assert(
            "Очень длинный промпт" not in msg,
            "промпт НЕ просочился в текст exception",
            errors,
        )
        _assert(
            len(msg) <= 300,
            f"текст короткий (≤300 симв, got {len(msg)})",
            errors,
        )
        _assert(
            "claude" not in msg.lower() or "code" in msg.lower(),
            "нет сырой команды ['claude', '-p', ...]",
            errors,
        )
        _assert(
            "900" in msg or "15 мин" in msg.lower() or "минут" in msg.lower(),
            f"упомянут таймаут / минуты (got: {msg[:120]!r})",
            errors,
        )


def test_timeout_not_retried(errors: list[str]) -> None:
    print("\n-- TimeoutExpired НЕ ретраится (не transient) --")
    calls = []

    def _count_and_raise(*a, **kw):
        calls.append(1)
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=900)

    import time as _time_mod
    with patch.object(hyperframes_broll.subprocess, "run", side_effect=_count_and_raise), \
         patch.object(_time_mod, "sleep", lambda *_: None):
        try:
            hyperframes_broll._run_claude("prompt")
        except Exception:
            pass
    _assert(
        len(calls) == 1,
        f"subprocess.run вызван 1 раз без retry (got {len(calls)})",
        errors,
    )


def main() -> int:
    print("=" * 60)
    print("test_hyperframes_timeout")
    print("=" * 60)
    errors: list[str] = []
    test_timeout_class_exists(errors)
    test_timeout_raises_hf_timeout(errors)
    test_timeout_message_does_not_leak_prompt(errors)
    test_timeout_not_retried(errors)
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
