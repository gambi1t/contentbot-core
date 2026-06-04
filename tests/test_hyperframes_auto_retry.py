"""TDD: _run_claude должен делать 1 авто-retry при SIGTERM (rc=143/-15/137/-9).

Контекст (Артём 31 мая):
  • Claude Code CLI на сервере живёт через CLAUDE_CODE_OAUTH_TOKEN
    (Max-подписка, flat-fee). Кредиты НЕ критерий для решений типа retry —
    см. reference_claude_code_server_subscription.md.
  • SIGTERM на subprocess `claude` — узкий, но реальный кейс (OOM-killer
    убил конкретно subprocess, не bot.py; либо ручной kill). Если bot.py
    жив — auto-retry успеет сработать и юзер вообще не увидит ошибку.

Контракт:
  • Первый вызов вернул SIGTERM → ждём ~3 сек → повторяем.
    Если второй вызов rc=0 → нет exception (юзер не увидел ошибку).
  • Если оба вызова SIGTERM → HyperFramesInterrupted с пометкой «после
    повторной попытки» (юзер видит «🔁 Повторить» через card_hfbroll).
  • rc=1 первый раз → exception сразу, БЕЗ retry (это не SIGTERM, retry
    бессмыслен — реальная ошибка повторится).
  • rc=0 первый раз → нет exception, никакого retry.

Run: python tests/test_hyperframes_auto_retry.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")
# Чтобы тесты не ждали реальные секунды, sleep делаем no-op
os.environ.setdefault("HF_RETRY_DELAY_SEC", "0")

sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _proc(rc: int, stdout: str = '{"total_cost_usd":0.0,"result":"x"}', stderr: str = "killed"):
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def _run_with_sequence(*procs):
    """Возвращает (exception | None, сколько раз subprocess.run был вызван)."""
    seq = list(procs)
    calls = []

    def _fake_run(*a, **kw):
        calls.append((a, kw))
        if not seq:
            raise AssertionError("subprocess.run вызван больше, чем ожидалось")
        return seq.pop(0)

    import time as _time_mod
    with patch.object(hyperframes_broll.subprocess, "run", side_effect=_fake_run), \
         patch.object(_time_mod, "sleep", lambda *_: None):
        try:
            hyperframes_broll._run_claude("prompt")
            return None, len(calls)
        except Exception as e:
            return e, len(calls)


def test_first_sigterm_then_success_no_exception(errors: list[str]) -> None:
    print("\n-- SIGTERM → retry → rc=0: юзер не видит ошибку --")
    exc, n_calls = _run_with_sequence(_proc(143), _proc(0))
    _assert(exc is None, f"нет exception (got {type(exc).__name__ if exc else None})", errors)
    _assert(n_calls == 2, f"subprocess.run вызван 2 раза (got {n_calls})", errors)


def test_two_sigterms_raises_interrupted(errors: list[str]) -> None:
    print("\n-- два SIGTERM подряд → HyperFramesInterrupted --")
    exc, n_calls = _run_with_sequence(_proc(143), _proc(-15))
    _assert(exc is not None, "exception поднят", errors)
    if exc:
        _assert(
            isinstance(exc, hyperframes_broll.HyperFramesInterrupted),
            f"тип = HyperFramesInterrupted (got {type(exc).__name__})",
            errors,
        )
    _assert(n_calls == 2, f"только 1 retry, всего 2 вызова (got {n_calls})", errors)


def test_rc1_no_retry(errors: list[str]) -> None:
    print("\n-- rc=1 (реальная ошибка Claude) → exception сразу, БЕЗ retry --")
    exc, n_calls = _run_with_sequence(_proc(1, stderr="rate limit"))
    _assert(exc is not None, "exception поднят", errors)
    if exc:
        _assert(
            isinstance(exc, hyperframes_broll.HyperFramesBrollError)
            and not isinstance(exc, hyperframes_broll.HyperFramesInterrupted),
            f"тип = HyperFramesBrollError (НЕ Interrupted, got {type(exc).__name__})",
            errors,
        )
    _assert(n_calls == 1, f"retry НЕ было (got {n_calls} calls)", errors)


def test_rc0_first_no_retry(errors: list[str]) -> None:
    print("\n-- rc=0 первый раз → нет retry, нет exception --")
    exc, n_calls = _run_with_sequence(_proc(0))
    _assert(exc is None, "нет exception", errors)
    _assert(n_calls == 1, f"один вызов, без retry (got {n_calls})", errors)


def test_message_after_retry_mentions_retry(errors: list[str]) -> None:
    print("\n-- сообщение после неудачного retry упоминает «после повторной попытки» --")
    exc, _ = _run_with_sequence(_proc(143), _proc(143))
    if exc:
        msg = str(exc).lower()
        _assert(
            "после повтор" in msg or "после retry" in msg or "повторная попытка" in msg,
            f"в тексте есть «после повтор/retry/попытка» (got: {str(exc)[:140]!r})",
            errors,
        )


def test_retry_delay_configurable(errors: list[str]) -> None:
    print("\n-- задержка retry конфигурируема через env HF_RETRY_DELAY_SEC --")
    # Если задержка configurable, должна быть константа/переменная в модуле
    # ИЛИ функция должна читать env. Проверяем что задержка не хардкодит
    # ноль и что мокабельна через time.sleep (что мы и делаем).
    _assert(
        hasattr(hyperframes_broll, "HF_RETRY_DELAY_SEC")
        or "HF_RETRY_DELAY_SEC" in Path(hyperframes_broll.__file__).read_text(encoding="utf-8"),
        "константа HF_RETRY_DELAY_SEC или env-чтение есть в модуле",
        errors,
    )


def main() -> int:
    print("=" * 60)
    print("test_hyperframes_auto_retry")
    print("=" * 60)
    errors: list[str] = []
    test_first_sigterm_then_success_no_exception(errors)
    test_two_sigterms_raises_interrupted(errors)
    test_rc1_no_retry(errors)
    test_rc0_first_no_retry(errors)
    test_message_after_retry_mentions_retry(errors)
    test_retry_delay_configurable(errors)
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
