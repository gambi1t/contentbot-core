"""TDD P2a (live-test 30.06): после «✅ Готово» в /ready пользователь дописал
текст (свой вариант описания) → бот ответил «💡 Идея принята. Что делаем?».

Корень: флоу описания callback-only, /ready держит state 'selfie_cover_picking',
которого нет в текстовом роутере; свободный текст падает в catch-all process_idea
(«любой текст = новая идея») → _show_pipeline_fork. То же для голоса (catch-all
шире) и видео (idea_text='').

Фикс: единый гейт _route_fresh_idea_or_reject — новую идею стартуем ТОЛЬКО из
_IDEA_INPUT_STATES = {None, '', 'pipeline_fork'} И при непустом тексте; иначе
вежливый отказ. Opt-in: новое callback-состояние по умолчанию отвергает текст,
а не утекает в idea-fork. Один хелпер для текста И голоса.

Запуск: python tests/test_idea_gate_p2a.py
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
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


class _FakeMsg:
    def __init__(self):
        self.replies = []
        self.edits = []

    async def reply_text(self, text, **kw):
        self.replies.append(text); return self

    async def edit_text(self, text, **kw):
        self.edits.append(text); return self


class _FakeUpdate:
    def __init__(self, uid=7):
        self.effective_user = SimpleNamespace(id=uid)
        self.message = _FakeMsg()


def _setup():
    bot.pending.clear()
    bot._save_pending = lambda p: None  # без записи на диск


def test_text_in_menu_state_rejected(errors):
    print("\n-- текст в callback-only состоянии (/ready cover) НЕ становится идеей --")
    _setup()
    bot.pending[7] = {"state": "selfie_cover_picking"}
    upd = _FakeUpdate(7)
    asyncio.run(bot._route_fresh_idea_or_reject(
        upd, None, "Закладки с курсами — это кладбище знаний", "selfie_cover_picking"))
    _assert(bot.pending.get(7, {}).get("state") != "pipeline_fork",
            "текст в меню-состоянии НЕ ушёл в idea-fork", errors)
    _assert(any("кнопк" in r.lower() for r in upd.message.replies),
            "показан вежливый отказ (жми кнопку)", errors)


def test_text_in_clean_state_forks(errors):
    print("\n-- текст из чистого состояния → развилка пайплайна (как раньше) --")
    _setup()
    upd = _FakeUpdate(7)
    asyncio.run(bot._route_fresh_idea_or_reject(upd, None, "моя новая идея про ИИ", None))
    _assert(bot.pending.get(7, {}).get("state") == "pipeline_fork",
            "чистое состояние + текст → idea-fork (не сломали быстрый ввод идеи)", errors)


def test_empty_text_not_idea(errors):
    print("\n-- пустое сообщение (видео/медиа без подписи) НЕ становится идеей --")
    _setup()
    upd = _FakeUpdate(7)
    asyncio.run(bot._route_fresh_idea_or_reject(upd, None, "", None))
    _assert(bot.pending.get(7, {}).get("state") != "pipeline_fork",
            "пустой текст из чистого состояния НЕ создал пустую идею", errors)


def test_pipeline_fork_state_replaces_idea(errors):
    print("\n-- на экране развилки текст заменяет идею (pipeline_fork остаётся idea-input) --")
    _setup()
    bot.pending[7] = {"state": "pipeline_fork", "fork_idea_text": "старая идея"}
    upd = _FakeUpdate(7)
    asyncio.run(bot._route_fresh_idea_or_reject(
        upd, None, "новая идея", "pipeline_fork"))
    _assert(bot.pending.get(7, {}).get("fork_idea_text") == "новая идея",
            "на развилке новый текст заменяет идею", errors)


def test_voice_in_menu_state_rejected_via_edit(errors):
    print("\n-- голос в меню-состоянии: отказ редактированием статус-сообщения --")
    _setup()
    bot.pending[7] = {"state": "broll2_manual"}
    upd = _FakeUpdate(7)
    status = _FakeMsg()
    asyncio.run(bot._route_fresh_idea_or_reject(
        upd, None, "расшифровка голоса", "broll2_manual", edit_msg=status))
    _assert(bot.pending.get(7, {}).get("state") != "pipeline_fork",
            "голос в меню-состоянии НЕ ушёл в idea-fork", errors)
    _assert(any("кнопк" in e.lower() for e in status.edits),
            "отказ показан через edit статус-сообщения (голос)", errors)


def main() -> int:
    errors: list = []
    for fn in (test_text_in_menu_state_rejected, test_text_in_clean_state_forks,
               test_empty_text_not_idea, test_pipeline_fork_state_replaces_idea,
               test_voice_in_menu_state_rejected_via_edit):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK all idea-gate tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
