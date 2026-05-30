"""Tests for SubscriptionClient — claude.messages.create через Claude Code CLI.

Чтобы НЕ дёргать реальный CLI в unit-тестах, мокаем subprocess.run через
monkeypatch — проверяем что:
  - команда собирается правильно (флаги, model, system_prompt, user message)
  - shape ответа совместим с anthropic.types.Message (response.content[0].text)
  - ANTHROPIC_API_KEY убирается из env (иначе CLI идёт через метеред API)
  - CLAUDE_CODE_OAUTH_TOKEN передаётся в env

Запуск:
  python tests/test_claude_subscription.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_subscription import SubscriptionClient  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    if not cond:
        errors.append(f"FAIL {safe}")
        print(f"  FAIL {safe}")
    else:
        print(f"  OK {safe}")


# ─── Fake subprocess.run ──────────────────────────────────────────────────

class _FakeProc:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


_last_call = {}  # captured by fake


def _fake_run_factory(response_text: str):
    """Возвращает fake subprocess.run который возвращает CLI-shape JSON."""
    def _fake_run(cmd, env=None, capture_output=None, text=None, timeout=None, **_):
        _last_call["cmd"] = list(cmd)
        _last_call["env"] = dict(env) if env else {}
        # Имитируем shape CLI ответа
        payload = json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1000,
            "result": response_text,
            "stop_reason": "end_turn",
            "total_cost_usd": 0.0123,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
        return _FakeProc(stdout=payload)
    return _fake_run


# ─── Tests ────────────────────────────────────────────────────────────────

def test_subscription_client_basic_call(errors: list[str]) -> None:
    print("\n-- SubscriptionClient.messages.create: basic shape --")
    import subprocess
    orig_run = subprocess.run
    subprocess.run = _fake_run_factory('{"hello": "world"}')
    try:
        client = SubscriptionClient(oauth_token="fake-token")
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system="You output JSON only.",
            messages=[{"role": "user", "content": "Give me JSON."}],
        )
        _assert(
            hasattr(resp, "content") and isinstance(resp.content, list),
            "response.content is list",
            errors,
        )
        if resp.content:
            _assert(
                hasattr(resp.content[0], "text") and resp.content[0].text == '{"hello": "world"}',
                f"response.content[0].text matches CLI result ({resp.content[0].text!r})",
                errors,
            )
        _assert(resp.model == "claude-sonnet-4-6", f"model passes through ({resp.model})", errors)
    finally:
        subprocess.run = orig_run


def test_subscription_client_cli_args(errors: list[str]) -> None:
    print("\n-- SubscriptionClient: правильные CLI флаги --")
    import subprocess
    orig_run = subprocess.run
    subprocess.run = _fake_run_factory("ok")
    try:
        client = SubscriptionClient(oauth_token="fake-token-XYZ")
        client.messages.create(
            model="claude-opus-4-7",
            max_tokens=5000,
            system="SYS",
            messages=[{"role": "user", "content": "USER"}],
        )
        cmd = _last_call.get("cmd", [])
        env = _last_call.get("env", {})
        _assert("claude" in cmd[0], f"command is claude ({cmd[0]})", errors)
        _assert("-p" in cmd, "-p flag for print mode", errors)
        _assert("--output-format" in cmd and "json" in cmd, "--output-format json", errors)
        _assert("--model" in cmd and "claude-opus-4-7" in cmd, "model passed via --model", errors)
        _assert("--system-prompt" in cmd and "SYS" in cmd, "system prompt passed", errors)
        _assert("USER" in cmd, "user message in cmd", errors)
        # OAuth должен попасть в env, API_KEY — нет
        _assert(
            env.get("CLAUDE_CODE_OAUTH_TOKEN") == "fake-token-XYZ",
            "OAuth token in env",
            errors,
        )
        _assert(
            "ANTHROPIC_API_KEY" not in env or not env.get("ANTHROPIC_API_KEY"),
            f"ANTHROPIC_API_KEY removed from env (got {env.get('ANTHROPIC_API_KEY')!r})",
            errors,
        )
    finally:
        subprocess.run = orig_run


def test_subscription_client_error_propagation(errors: list[str]) -> None:
    print("\n-- SubscriptionClient: CLI error → exception --")
    import subprocess
    orig_run = subprocess.run

    def _fail(*_, **__):
        return _FakeProc(stdout="", returncode=1, stderr="auth failed")

    subprocess.run = _fail
    try:
        client = SubscriptionClient(oauth_token="bad")
        try:
            client.messages.create(
                model="sonnet", max_tokens=10, system="", messages=[{"role": "user", "content": "x"}],
            )
            _assert(False, "should raise on rc != 0", errors)
        except Exception as e:
            _assert(
                "auth failed" in str(e) or "rc=1" in str(e),
                f"error message contains stderr ({e!r})",
                errors,
            )
    finally:
        subprocess.run = orig_run


def test_subscription_client_messages_concat(errors: list[str]) -> None:
    """Multi-message: concat в один user prompt с разделителем."""
    print("\n-- SubscriptionClient: multi-message concat --")
    import subprocess
    orig_run = subprocess.run
    subprocess.run = _fake_run_factory("response")
    try:
        client = SubscriptionClient(oauth_token="x")
        client.messages.create(
            model="sonnet", max_tokens=10, system="",
            messages=[
                {"role": "user", "content": "Part A"},
                {"role": "user", "content": "Part B"},
            ],
        )
        cmd = _last_call.get("cmd", [])
        # Обе части должны быть в prompt
        full_prompt = " ".join(str(c) for c in cmd)
        _assert("Part A" in full_prompt, "Part A in prompt", errors)
        _assert("Part B" in full_prompt, "Part B in prompt", errors)
    finally:
        subprocess.run = orig_run


def test_subscription_client_no_oauth_raises(errors: list[str]) -> None:
    print("\n-- SubscriptionClient(oauth_token='') → raise --")
    try:
        SubscriptionClient(oauth_token="")
        _assert(False, "empty OAuth should raise", errors)
    except ValueError:
        _assert(True, "empty OAuth raises ValueError", errors)
    except Exception as e:
        _assert(True, f"empty OAuth raises ({type(e).__name__})", errors)


def main() -> int:
    print("=" * 60)
    print("SubscriptionClient tests")
    print("=" * 60)
    errors: list[str] = []
    test_subscription_client_basic_call(errors)
    test_subscription_client_cli_args(errors)
    test_subscription_client_error_propagation(errors)
    test_subscription_client_messages_concat(errors)
    test_subscription_client_no_oauth_raises(errors)
    print("\n" + "=" * 60)
    if errors:
        print(f"Found {len(errors)} failure(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK all SubscriptionClient tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
