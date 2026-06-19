"""TDD: B-roll Pipeline 2 — гейт 5: название/хук поста.

Артём: довести Pipeline 2 до паритета. Гейт 5 — на финал-экране кнопка «✍️
Название/хук»: генерим 5 хуков из сценария (реюз движка _generate_hook_options,
brand-aware, тот же что в селфи) → пользователь выбирает / ещё / свой → выбранный
заголовок сохраняется в broll_draft['title'] и проставляется в Notion-карточку
(свойство «Name»). Движок и Notion-паттерн — DI из bot.py (как cover_fn/publish_fn).

UI селфи (_selfie_hook_keyboard, selfie_hook_pick) selfie-coupled → тонкий b2title.
Запуск: python tests/test_broll_title.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import broll.handlers as bh  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _cbs(markup):
    if markup is None:
        return []
    return [getattr(b, "callback_data", None) for row in markup.inline_keyboard for b in row]


class _FakeMsg:
    async def delete(self): pass
    async def edit_text(self, *a, **k): pass


class _FakeBot:
    def __init__(self):
        self.sends = []
    async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
        self.sends.append({"text": text, "reply_markup": reply_markup}); return _FakeMsg()


DID = "broll_7_1700000000000"


def _ctx(draft):
    return SimpleNamespace(bot=_FakeBot(), user_data={"broll_draft": draft, "broll_draft_id": DID})


def _upd():
    return SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=42),
                           callback_query=SimpleNamespace(message=SimpleNamespace(chat_id=42)), message=None)


def _draft(**extra):
    d = {"script": "Сценарий про зимний глэмпинг.", "theme": "глэмпинг", "chat_id": 42,
         "notion_page_id": "pg1", "draft_id": DID, "final_path": "/tmp/f.mp4"}
    d.update(extra); return d


HOOKS = ["Глэмпинг зимой — не сказка", "80% новичков делают эту ошибку", "Я думал отель лучше. Зря."]


def _hook_fn(source_text, exclude_hooks=None, n=5):
    ex = set(exclude_hooks or [])
    return [h for h in HOOKS if h not in ex][:n]


# ── Тесты ────────────────────────────────────────────────────────────

def test_title_keyboard(errors):
    print("\n[_title_pick_keyboard — хуки + ещё + свой + broll_cancel]")
    kb = bh._title_pick_keyboard(DID, HOOKS)
    cbs = _cbs(kb)
    _assert(f"b2title:pick:{DID}:0" in cbs, f"кнопка выбора хука 0: {cbs}", errors)
    _assert(f"b2title:pick:{DID}:2" in cbs, "кнопка выбора хука 2", errors)
    _assert(f"b2title:more:{DID}" in cbs, "кнопка «Ещё варианты»", errors)
    _assert(f"b2title:own:{DID}" in cbs, "кнопка «Свой»", errors)
    _assert("broll_cancel" in cbs, "реюз broll_cancel", errors)


def test_start_generates_and_shows(errors):
    print("\n[start_broll_title_pick — генерит хуки из сценария + показывает]")
    ctx = _ctx(_draft())
    asyncio.run(bh.start_broll_title_pick(_upd(), ctx, DID, hook_fn=_hook_fn, chat_id=42))
    d = ctx.user_data["broll_draft"]
    _assert(d.get("title_options") == HOOKS, f"хуки сохранены: {d.get('title_options')}", errors)
    cbs = [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(any(str(c).startswith("b2title:pick:") for c in cbs), "клавиатура хуков показана", errors)


def test_pick_stores_title_and_sets_notion(errors):
    print("\n[b2title:pick — заголовок в черновик + Notion-карточку]")
    patched = {}
    ctx = _ctx(_draft(title_options=HOOKS))
    asyncio.run(bh.handle_broll_title_cb(
        _upd(), ctx, "pick", DID, arg="1", hook_fn=_hook_fn,
        notion_title_fn=lambda pid, t: patched.update({"pid": pid, "title": t}), chat_id=42))
    _assert(ctx.user_data["broll_draft"].get("title") == HOOKS[1], "выбранный хук сохранён в broll_draft['title']", errors)
    _assert(patched.get("pid") == "pg1" and patched.get("title") == HOOKS[1], "Notion-заголовок проставлен", errors)
    all_text = " ".join(s["text"] or "" for s in ctx.bot.sends)
    _assert(HOOKS[1] in all_text, "подтверждение с выбранным заголовком", errors)


def test_more_regenerates_excluding_shown(errors):
    print("\n[b2title:more — новые варианты, исключая показанные]")
    calls = {}
    def _hf(source, exclude_hooks=None, n=5):
        calls["exclude"] = list(exclude_hooks or []); return _hook_fn(source, exclude_hooks, n)
    ctx = _ctx(_draft(title_options=HOOKS[:1], title_shown=HOOKS[:1]))
    asyncio.run(bh.handle_broll_title_cb(_upd(), ctx, "more", DID, hook_fn=_hf,
                                         notion_title_fn=lambda *a: None, chat_id=42))
    _assert(HOOKS[0] in calls.get("exclude", []), f"генерация исключает показанные: {calls.get('exclude')}", errors)


def test_own_enters_state_and_message(errors):
    print("\n[b2title:own → state broll2_title_text → текст сохраняется]")
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    ctx = _ctx(_draft())
    asyncio.run(bh.handle_broll_title_cb(_upd(), ctx, "own", DID, hook_fn=_hook_fn,
                                         notion_title_fn=lambda *a: None, chat_id=42))
    _assert(bh._bot_pending.get(7, {}).get("state") == "broll2_title_text", "состояние ввода своего заголовка", errors)
    # приём текста
    patched = {}
    ctx2 = _ctx(_draft())
    bh._bot_pending = {7: {"state": "broll2_title_text", "title_draft_id": DID}}
    upd2 = _upd(); upd2.message = SimpleNamespace(text="Мой свой заголовок")
    handled = asyncio.run(bh.handle_broll_title_text_message(
        upd2, ctx2, notion_title_fn=lambda pid, t: patched.update({"pid": pid, "title": t})))
    _assert(handled is True, "сообщение обработано", errors)
    _assert(ctx2.user_data["broll_draft"].get("title") == "Мой свой заголовок", "свой заголовок сохранён", errors)
    _assert(patched.get("title") == "Мой свой заголовок", "свой заголовок → Notion", errors)


def test_bot_wiring(errors):
    print("\n[bot.py — кнопка названия на финале + ветка b2title + роут текста]")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    _assert("start_broll_title_pick" in src, "финал зовёт start_broll_title_pick", errors)
    _assert('"b2title:"' in src or "'b2title:'" in src, "ветка b2title в handle_callback", errors)
    _assert("broll2_title_text" in src, "роут broll2_title_text в message handler", errors)


def main():
    errors = []
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_title_test_"))
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    test_title_keyboard(errors)
    test_start_generates_and_shows(errors)
    test_pick_stores_title_and_sets_notion(errors)
    test_more_regenerates_excluding_shown(errors)
    test_own_enters_state_and_message(errors)
    test_bot_wiring(errors)
    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors: print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
