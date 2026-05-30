"""Drop-in замена `anthropic.Anthropic` через Claude Code CLI с OAuth-подпиской.

Цель: убрать pay-per-token billing на ANTHROPIC_API_KEY для бот-вызовов
(Opus/Sonnet/Haiku). Раньше каждый `claude.messages.create(...)` шёл через
`anthropic.Anthropic(api_key=...)` → метеред Anthropic billing. Теперь под
капотом subprocess вызов `claude` CLI с `CLAUDE_CODE_OAUTH_TOKEN` — это
подписка (Max/Pro план), вызовы покрываются flat fee.

Совместимость: возвращаемый объект мимикрирует `anthropic.types.Message`
ровно настолько, насколько использует наш код — `response.content[0].text`,
`response.model`, `response.usage`. Этого хватает для 25+ call-sites в
bot.py + carousel/llm.py + idea_generator.py + broll/selector.py.

Auth flow повторяет паттерн из `auto_broll.py:_run_claude`:
- ANTHROPIC_API_KEY УБИРАЕТСЯ из env дочернего процесса (иначе CLI
  предпочтёт API ключ перед OAuth → подписка не сработает).
- CLAUDE_CODE_OAUTH_TOKEN передаётся явно.

Использование (в bot.py):
    oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if oauth_token:
        from claude_subscription import SubscriptionClient
        claude = SubscriptionClient(oauth_token=oauth_token)
    else:
        claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
"""
from __future__ import annotations

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


_CLI_TIMEOUT_SEC = 180
_DEFAULT_MAX_BUDGET_USD = "1.0"   # safety cap — подписка покрывает, но fallback метеред пути не выйдет за $1/вызов
_DEFAULT_DISALLOWED_TOOLS = (
    # Запрещаем все tools — нам нужен только текст-generation, не agent.
    "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task"
)


class _ContentItem:
    """Mimics anthropic.types.TextBlock — поле `.text` + `.type`."""

    __slots__ = ("text", "type")

    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class _Response:
    """Mimics anthropic.types.Message — `.content` (list of TextBlock),
    `.model`, `.usage` (dict), `.stop_reason`.
    """

    __slots__ = ("content", "model", "usage", "stop_reason")

    def __init__(self, text: str, model: str, usage: dict | None = None,
                 stop_reason: str = "end_turn"):
        self.content = [_ContentItem(text)]
        self.model = model
        self.usage = usage or {}
        self.stop_reason = stop_reason


class _Messages:
    """Mimics anthropic.Anthropic().messages — метод create."""

    def __init__(self, client: "SubscriptionClient"):
        self._client = client

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict],
        system: str | None = None,
        **_unused,
    ) -> _Response:
        """Эквивалент anthropic.Anthropic().messages.create — через CLI.

        Поддерживаем подмножество API:
          - `model` (имя модели или alias 'sonnet'/'opus'/'haiku')
          - `max_tokens` (игнорится в CLI — он сам решает; передаём для совместимости)
          - `system` (str, передаётся через --system-prompt)
          - `messages` (list of {role, content}) — конкатенируем user-сообщения
            в один prompt. Multi-turn assistant prefill НЕ поддерживается.

        Возвращает `_Response` совместимый по shape с anthropic.types.Message.
        """
        user_text = self._extract_user_text(messages)
        if not user_text:
            raise ValueError("messages must contain at least one user message with text")

        env = dict(os.environ)
        # Critical: убираем ANTHROPIC_API_KEY чтобы CLI не пошёл по метеред пути.
        env.pop("ANTHROPIC_API_KEY", None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = self._client._oauth_token
        # CLI требует HOME для записи session-state.
        env.setdefault("HOME", "/tmp")

        cmd: list[str] = [
            "claude", "-p", user_text,
            "--output-format", "json",
            "--model", model,
            "--disallowedTools", _DEFAULT_DISALLOWED_TOOLS,
            "--max-budget-usd", _DEFAULT_MAX_BUDGET_USD,
            "--no-session-persistence",   # не сохраняем session — мы stateless
        ]
        if system:
            cmd.extend(["--system-prompt", system])

        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=_CLI_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Claude CLI timeout after {_CLI_TIMEOUT_SEC}s (model={model})"
            ) from e

        if proc.returncode != 0:
            stderr_snip = (proc.stderr or "")[:500]
            raise RuntimeError(
                f"Claude CLI failed (rc={proc.returncode}, model={model}): {stderr_snip}"
            )

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            stdout_snip = (proc.stdout or "")[:300]
            raise RuntimeError(
                f"Claude CLI returned invalid JSON: {e}. stdout: {stdout_snip!r}"
            ) from e

        if data.get("is_error"):
            raise RuntimeError(
                f"Claude CLI api_error: {data.get('api_error_status')!r}, "
                f"subtype={data.get('subtype')}"
            )

        result_text = str(data.get("result", "") or "")
        usage = data.get("usage", {}) or {}
        cost = float(data.get("total_cost_usd", 0.0) or 0.0)
        logger.info(
            f"[claude-cli] model={model} subscription_call ok, "
            f"cost_equiv=${cost:.4f}, "
            f"in={usage.get('input_tokens')}, out={usage.get('output_tokens')}, "
            f"duration_ms={data.get('duration_ms')}"
        )
        return _Response(
            text=result_text,
            model=model,
            usage=usage,
            stop_reason=str(data.get("stop_reason", "end_turn") or "end_turn"),
        )

    @staticmethod
    def _extract_user_text(messages: list[dict]) -> str:
        """Conкатенирует все user-сообщения в один prompt.

        Anthropic API формат: [{role, content}], где content либо str, либо
        list of {type: 'text', text: ...}. Поддерживаем оба.
        Assistant prefill (последнее сообщение с role='assistant') в CLI
        не имеет аналога — игнорируем с warning.
        """
        if not isinstance(messages, list):
            return ""
        parts: list[str] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "assistant":
                logger.warning(
                    "[claude-cli] assistant prefill in messages ignored "
                    "(CLI doesn't support it)"
                )
                continue
            # content может быть str или list of blocks
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "") or ""
                        if text:
                            parts.append(text)
                    elif isinstance(block, str):
                        parts.append(block)
        return "\n\n".join(p for p in parts if p)


class SubscriptionClient:
    """Drop-in замена `anthropic.Anthropic` через Claude Code CLI.

    Использует подписку (Max/Pro) через `CLAUDE_CODE_OAUTH_TOKEN` —
    вызовы не идут по pay-per-token.
    """

    def __init__(self, oauth_token: str):
        if not oauth_token or not isinstance(oauth_token, str):
            raise ValueError(
                "SubscriptionClient requires non-empty CLAUDE_CODE_OAUTH_TOKEN"
            )
        self._oauth_token = oauth_token
        self.messages = _Messages(self)


__all__ = ["SubscriptionClient"]
