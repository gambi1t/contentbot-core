"""TDD: хелпер `_send_or_edit` должен фолбэкнуться в send_message,
если у исходного сообщения нет text (видео/фото) и edit_message_text
бросает BadRequest("There is no text in the message to edit").

Баг (Артём 31 мая): после генерации аватара (видео-сообщение с caption)
нажатие «Подобрать B-roll» → handler делает query.edit_message_text → Telegram
отбивает BadRequest. Лог: bot.py:14432.

Run: python tests/test_send_or_edit_fallback.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402
from telegram.error import BadRequest  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


class _FakeBot:
    def __init__(self) -> None:
        self.send_calls: list[dict] = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.send_calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return SimpleNamespace(message_id=999)


class _FakeQuery:
    def __init__(self, edit_should_fail_no_text: bool) -> None:
        self._fail = edit_should_fail_no_text
        self.edit_calls: list[dict] = []
        self.message = SimpleNamespace(chat_id=42)

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edit_calls.append({"text": text, "reply_markup": reply_markup})
        if self._fail:
            raise BadRequest("There is no text in the message to edit")
        return SimpleNamespace(message_id=1)


def test_helper_exists(errors: list[str]) -> None:
    print("\n-- _send_or_edit существует --")
    _assert(callable(getattr(bot, "_send_or_edit", None)), "bot._send_or_edit callable", errors)


def test_fallback_on_no_text(errors: list[str]) -> None:
    print("\n-- видео-сообщение: edit фейлится → send_message --")
    q = _FakeQuery(edit_should_fail_no_text=True)
    ctx = SimpleNamespace(bot=_FakeBot())
    asyncio.run(bot._send_or_edit(q, ctx, "MENU", reply_markup="KB"))
    _assert(len(q.edit_calls) == 1, "edit попробован", errors)
    _assert(len(ctx.bot.send_calls) == 1, "send_message вызван", errors)
    if ctx.bot.send_calls:
        call = ctx.bot.send_calls[0]
        _assert(call["chat_id"] == 42, "chat_id передан", errors)
        _assert(call["text"] == "MENU", "text передан", errors)
        _assert(call["reply_markup"] == "KB", "reply_markup передан", errors)


def test_normal_edit_path(errors: list[str]) -> None:
    print("\n-- текстовое сообщение: edit проходит, send_message НЕ вызывается --")
    q = _FakeQuery(edit_should_fail_no_text=False)
    ctx = SimpleNamespace(bot=_FakeBot())
    asyncio.run(bot._send_or_edit(q, ctx, "MENU", reply_markup="KB"))
    _assert(len(q.edit_calls) == 1, "edit вызван", errors)
    _assert(len(ctx.bot.send_calls) == 0, "send_message НЕ вызван (fallback не нужен)", errors)


def test_other_bad_request_propagates(errors: list[str]) -> None:
    print("\n-- другие BadRequest (не 'no text') пропагируются --")

    class _Q(_FakeQuery):
        async def edit_message_text(self, text, reply_markup=None, **kw):
            self.edit_calls.append({"text": text})
            raise BadRequest("Message is not modified")

    q = _Q(False)
    ctx = SimpleNamespace(bot=_FakeBot())
    # «Message is not modified» — известно безобидное, его глотаем.
    # А вот что-то незнакомое — должно пробрасываться.
    # Подменим на незнакомое:
    class _Q2(_FakeQuery):
        async def edit_message_text(self, text, reply_markup=None, **kw):
            self.edit_calls.append({"text": text})
            raise BadRequest("Chat not found")

    q2 = _Q2(False)
    ctx2 = SimpleNamespace(bot=_FakeBot())
    raised = False
    try:
        asyncio.run(bot._send_or_edit(q2, ctx2, "x"))
    except BadRequest:
        raised = True
    _assert(raised, "незнакомый BadRequest пробрасывается", errors)


def main() -> int:
    print("=" * 60)
    print("test_send_or_edit_fallback")
    print("=" * 60)
    errors: list[str] = []
    test_helper_exists(errors)
    if not callable(getattr(bot, "_send_or_edit", None)):
        print("\nFAIL: хелпер не определён — остальные тесты не выполняются")
        return 1
    test_fallback_on_no_text(errors)
    test_normal_edit_path(errors)
    test_other_bad_request_propagates(errors)
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
