"""TDD: _run_claude должен ОТЛИЧАТЬ SIGTERM (внешний kill) от реального
падения Claude Code CLI.

Баг (Артём 31 мая, скрин): после моего systemd-restart maksim-bot в момент
работы HyperFrames Claude Code получил SIGTERM (rc=143). Юзер увидел
пугающее «⚠️ Не удалось сгенерировать графику (HyperFrames): Claude Code
упал (rc=143)» — хотя это не баг Claude, а инфра-событие.

Контракт фикса:
  • subprocess.returncode ∈ {143, -15, 137, -9} → HyperFramesInterrupted
    (наследник HyperFramesBrollError — обратная совместимость для catch-all)
  • subprocess.returncode == 1 или другие → HyperFramesBrollError (как было)
  • subprocess.returncode == 0 → нет exception

В bot.py card_hfbroll handler ловит HyperFramesInterrupted отдельно и
показывает «🔁 Сервис был перезапущен» + retry-кнопку (тестируется
отдельно — здесь только классификация ошибки на уровне subprocess).

Run: python tests/test_hyperframes_sigterm_classification.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
# CLAUDE_CODE_OAUTH_TOKEN опционален — _run_claude фолбэчится на API-ключ
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _fake_proc(rc: int, stderr: str = "", stdout: str = ""):
    return SimpleNamespace(returncode=rc, stderr=stderr, stdout=stdout)


def _call_run_claude_with_rc(rc: int, stderr: str = "killed externally"):
    """Возвращает (поднятый exception | None, его тип-имя)."""
    fake = _fake_proc(rc, stderr=stderr, stdout='{"total_cost_usd":0.0,"result":"x"}')
    with patch.object(hyperframes_broll.subprocess, "run", return_value=fake):
        try:
            hyperframes_broll._run_claude("dummy prompt")
        except Exception as e:
            return e, type(e).__name__
    return None, None


def test_interrupted_class_exists(errors: list[str]) -> None:
    print("\n-- HyperFramesInterrupted exists и наследует HyperFramesBrollError --")
    cls = getattr(hyperframes_broll, "HyperFramesInterrupted", None)
    _assert(cls is not None, "класс HyperFramesInterrupted определён", errors)
    if cls:
        _assert(
            issubclass(cls, hyperframes_broll.HyperFramesBrollError),
            "наследник HyperFramesBrollError (обратная совместимость catch-all)",
            errors,
        )


def test_sigterm_143_classified(errors: list[str]) -> None:
    print("\n-- rc=143 (POSIX SIGTERM) → HyperFramesInterrupted --")
    exc, name = _call_run_claude_with_rc(143)
    _assert(exc is not None, "exception поднят", errors)
    if exc:
        _assert(
            isinstance(exc, getattr(hyperframes_broll, "HyperFramesInterrupted", type(None))),
            f"тип = HyperFramesInterrupted (got {name})",
            errors,
        )


def test_negative_15_classified(errors: list[str]) -> None:
    print("\n-- rc=-15 (Python signed SIGTERM) → HyperFramesInterrupted --")
    exc, name = _call_run_claude_with_rc(-15)
    _assert(exc is not None, "exception поднят", errors)
    if exc:
        _assert(
            isinstance(exc, getattr(hyperframes_broll, "HyperFramesInterrupted", type(None))),
            f"тип = HyperFramesInterrupted (got {name})",
            errors,
        )


def test_sigkill_137_classified(errors: list[str]) -> None:
    print("\n-- rc=137 (SIGKILL, e.g. OOM) → HyperFramesInterrupted --")
    exc, name = _call_run_claude_with_rc(137)
    _assert(exc is not None, "exception поднят", errors)
    if exc:
        _assert(
            isinstance(exc, getattr(hyperframes_broll, "HyperFramesInterrupted", type(None))),
            f"тип = HyperFramesInterrupted (got {name})",
            errors,
        )


def test_negative_9_classified(errors: list[str]) -> None:
    print("\n-- rc=-9 (Python signed SIGKILL) → HyperFramesInterrupted --")
    exc, name = _call_run_claude_with_rc(-9)
    _assert(exc is not None, "exception поднят", errors)
    if exc:
        _assert(
            isinstance(exc, getattr(hyperframes_broll, "HyperFramesInterrupted", type(None))),
            f"тип = HyperFramesInterrupted (got {name})",
            errors,
        )


def test_real_failure_rc1_NOT_interrupted(errors: list[str]) -> None:
    print("\n-- rc=1 (реальная ошибка Claude Code) → HyperFramesBrollError, НЕ Interrupted --")
    exc, name = _call_run_claude_with_rc(1, stderr="API rate limit exceeded")
    _assert(exc is not None, "exception поднят", errors)
    Interrupted = getattr(hyperframes_broll, "HyperFramesInterrupted", None)
    if exc and Interrupted:
        _assert(
            isinstance(exc, hyperframes_broll.HyperFramesBrollError),
            "это HyperFramesBrollError",
            errors,
        )
        _assert(
            not isinstance(exc, Interrupted),
            f"это НЕ HyperFramesInterrupted (got {name})",
            errors,
        )


def test_rc0_no_exception(errors: list[str]) -> None:
    print("\n-- rc=0 → нет exception, парсит cost --")
    exc, name = _call_run_claude_with_rc(0)
    _assert(exc is None, f"нет exception (got {name})", errors)


def test_message_mentions_interrupted(errors: list[str]) -> None:
    print("\n-- сообщение HyperFramesInterrupted информативно (без 'упал') --")
    exc, _ = _call_run_claude_with_rc(143)
    if exc:
        msg = str(exc)
        _assert(
            "перезапуск" in msg.lower() or "прерван" in msg.lower() or "interrupted" in msg.lower(),
            f"сообщение содержит 'перезапуск/прерван/interrupted' (got: {msg[:120]!r})",
            errors,
        )


def main() -> int:
    print("=" * 60)
    print("test_hyperframes_sigterm_classification")
    print("=" * 60)
    errors: list[str] = []
    test_interrupted_class_exists(errors)
    test_sigterm_143_classified(errors)
    test_negative_15_classified(errors)
    test_sigkill_137_classified(errors)
    test_negative_9_classified(errors)
    test_real_failure_rc1_NOT_interrupted(errors)
    test_rc0_no_exception(errors)
    test_message_mentions_interrupted(errors)
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
