"""Тест авто-ретрая Claude CLI (9 июня).

Артём: ИИ-монтаж/сценарий падали с rc=143 (рестарт-гонка/таймаут) → юзер
переделывал. Ретрай ТОЛЬКО на транзиентные (timeout, rc 143/137); обычные
ошибки (auth/bad-args) — fail-fast (без ретрая, не маскируем баги).

Запуск: python tests/test_claude_retry.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import claude_subscription as cs  # noqa: E402

_OK_JSON = json.dumps({"result": "ok-text", "usage": {}, "total_cost_usd": 0.0,
                       "stop_reason": "end_turn"})


class _FakeProc:
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode = rc; self.stdout = stdout; self.stderr = stderr


def _make_run(behaviors):
    """behaviors: список 'timeout' | 'ok' | int(rc<0 ошибка). Возвращает (fn, calls)."""
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        i = calls["n"]; calls["n"] += 1
        b = behaviors[min(i, len(behaviors) - 1)]
        if b == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        if b == "ok":
            return _FakeProc(0, stdout=_OK_JSON)
        return _FakeProc(int(b), stderr="boom")  # ошибочный rc
    return fake_run, calls


def _call():
    c = cs.SubscriptionClient("dummy-token")
    return c.messages.create(model="m", max_tokens=10,
                             messages=[{"role": "user", "content": "hi"}])


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    _orig_run, _orig_sleep = cs.subprocess.run, cs.time.sleep
    cs.time.sleep = lambda *a, **k: None  # без реальных пауз
    try:
        print("\n[timeout → ретрай → успех]")
        cs.subprocess.run, calls = _make_run(["timeout", "ok"])
        r = _call()
        _assert(r.content[0].text == "ok-text", "вернул успех после таймаута", errors)
        _assert(calls["n"] == 2, f"2 попытки (ретрай был), got {calls['n']}", errors)

        print("\n[rc=143 → ретрай → успех]")
        cs.subprocess.run, calls = _make_run([143, "ok"])
        r = _call()
        _assert(r.content[0].text == "ok-text", "успех после транзиентного 143", errors)
        _assert(calls["n"] == 2, f"2 попытки, got {calls['n']}", errors)

        print("\n[rc=1 обычная ошибка → БЕЗ ретрая (fail-fast)]")
        cs.subprocess.run, calls = _make_run([1, "ok"])
        raised = False
        try:
            _call()
        except RuntimeError:
            raised = True
        _assert(raised, "обычная ошибка → RuntimeError", errors)
        _assert(calls["n"] == 1, f"НЕ ретраил обычную ошибку (1 вызов), got {calls['n']}", errors)

        print("\n[timeout дважды → падает после исчерпания]")
        cs.subprocess.run, calls = _make_run(["timeout", "timeout"])
        raised = False
        try:
            _call()
        except RuntimeError:
            raised = True
        _assert(raised, "двойной таймаут → RuntimeError", errors)
        _assert(calls["n"] == 2, f"исчерпал 2 попытки, got {calls['n']}", errors)
    finally:
        cs.subprocess.run, cs.time.sleep = _orig_run, _orig_sleep

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
