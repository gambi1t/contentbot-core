"""TDD P3-крах (live-test 30.06): кнопка «Другой текст обложки» мёртвая —
ничего не происходит.

Корень: cover_redo_text (и сиблинги) зовут query.edit_message_text на
сообщении-ФОТО (cover-превью отправлено send_photo) → BadRequest «no text in
the message to edit» → молчаливый error_handler глотает → кнопка «мёртвая».

Фикс:
1) error_handler уведомляет пользователя (а не только логирует) — страховочная
   сеть для ЛЮБОЙ непокрытой точки входа (edit-on-photo и пр.).
2) Статус-edit на фото в cover/avatar-флоу идут через готовый _send_or_edit
   (edit → фолбэк send_message при «no text»): cover_redo_text, avatar_pick
   (явный файл), cover_notext, cover_ok.

Запуск: python tests/test_dead_button_p3.py
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


def test_error_handler_notifies_user(errors):
    print("\n-- error_handler уведомляет пользователя (не молчит) --")
    sent = []

    class _Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text)); return SimpleNamespace(message_id=1)

    upd = SimpleNamespace(effective_chat=SimpleNamespace(id=42))
    ctx = SimpleNamespace(bot=_Bot(), error=Exception("boom"))
    asyncio.run(bot.error_handler(upd, ctx))
    _assert(len(sent) == 1 and sent[0][0] == 42,
            "при необработанной ошибке шлёт сообщение в чат (страховочная сеть)", errors)


def test_error_handler_safe_without_chat(errors):
    print("\n-- error_handler не падает, если чата нет --")
    ctx = SimpleNamespace(bot=SimpleNamespace(), error=Exception("x"))
    crashed = False
    try:
        asyncio.run(bot.error_handler(SimpleNamespace(effective_chat=None), ctx))
    except Exception as e:
        crashed = True; print("   raised:", e)
    _assert(not crashed, "error_handler без чата не бросает", errors)


def test_cover_status_edits_routed_safe(errors):
    print("\n-- статус-edit на фото в cover/avatar идут через _send_or_edit --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    safe_needles = [
        '_send_or_edit(query, context, "🖼 Генерирую новые варианты обложки..."',  # cover_redo_text (доминанта)
        '_send_or_edit(query, context, "🖼 Генерирую варианты обложки..."',          # avatar_pick (явный файл)
        '_send_or_edit(query, context, "Готовлю обложку без текста',                 # cover_notext
        '_send_or_edit(query, context, "🖼 Генерирую обложку..."',                   # cover_ok
    ]
    for n in safe_needles:
        _assert(n in src, f"маршрутизировано через _send_or_edit: {n[:55]}…", errors)
    # доминанта больше НЕ зовёт сырой edit на фото
    _assert('query.edit_message_text("🖼 Генерирую новые варианты обложки...")' not in src,
            "cover_redo_text не зовёт сырой query.edit_message_text", errors)


def main() -> int:
    errors: list = []
    for fn in (test_error_handler_notifies_user, test_error_handler_safe_without_chat,
               test_cover_status_edits_routed_safe):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK all dead-button-P3 tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
