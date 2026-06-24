"""TDD: B-roll Pipeline 2 — гейт правки/утверждения сценария (инкремент 1).

Артём (17 июня): довести Pipeline 2 до уровня стандартного пайплайна —
пошаговый контроль. Гейт #1: ПОСЛЕ генерации сценария показать его с кнопками
«✏️ Править / ✅ Утвердить» ДО меню источника видеоряда. Правка — свободный
текст любого объёма (B-roll сценарий не привязан к таймкодам субтитров, как
селфи), БЕЗ валидации количества слов.

Канон этапов: memory/branches/maksim-bot/pipeline_stage_spec.md
Реюз-паттерн: selfie 2-state edit (callback ставит состояние → след. текст
применяет), но БЕЗ selfie.edit.apply_user_edits (там word-count gate под
таймкоды Whisper — для B-roll вреден).

Запуск: python tests/test_broll_script_edit.py
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
from broll.draft import BrollDraft, Status, save_draft, load_draft, new_draft_id  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _cbs(markup):
    """Плоский список callback_data из InlineKeyboardMarkup."""
    if markup is None:
        return []
    return [getattr(b, "callback_data", None) for row in markup.inline_keyboard for b in row]


# ── Фейки Telegram ───────────────────────────────────────────────────

class _FakeMsg:
    def __init__(self):
        self.deleted = False
        self.edits = []

    async def delete(self):
        self.deleted = True

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class _FakeBot:
    def __init__(self):
        self.sends = []  # [{chat_id, text, reply_markup}]

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return _FakeMsg()


def _ctx(user_data=None):
    return SimpleNamespace(bot=_FakeBot(), user_data=user_data if user_data is not None else {})


def _update(uid=7):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(id=42),
        callback_query=None,
        message=None,
    )


def _seed_draft(uid=7, chat_id=42, script="Один два три четыре пять"):
    did = new_draft_id(uid, 1_700_000_000.0)
    draft = BrollDraft(
        draft_id=did, user_id=uid, chat_id=chat_id,
        status=Status.AWAITING_SOURCE, source_mode=None,
        script_text=script, voice_estimate_sec=0.0, source_items=[],
        work_dir="", notion_url=None, notion_page_id=None,
        theme="тест", created_at=1_700_000_000.0, updated_at=1_700_000_000.0,
    )
    save_draft(draft, bh.DRAFTS_DIR)
    return did


# ── Тесты ────────────────────────────────────────────────────────────

def test_gate_keyboard(errors):
    print("\n[_script_gate_keyboard — Править/Утвердить, отмена реюзит broll_cancel]")
    kb = bh._script_gate_keyboard("broll_7_123")
    cbs = _cbs(kb)
    _assert("b2scr:edit:broll_7_123" in cbs, f"кнопка Править (b2scr:edit:<id>): {cbs}", errors)
    _assert("b2scr:ok:broll_7_123" in cbs, "кнопка Утвердить (b2scr:ok:<id>)", errors)
    _assert("broll_cancel" in cbs, "отмена реюзит существующий broll_cancel", errors)
    _assert(not any(str(c).startswith("b2src:") for c in cbs),
            "на гейте НЕТ меню источника (b2src) — оно после утверждения", errors)


def test_generate_preview_shows_gate_not_source(errors):
    print("\n[generate_broll_preview — показывает ГЕЙТ, а не меню источника]")
    bh.generate_script = lambda claude, theme, **kw: "Сценарий из ровно пяти слов тут"  # monkeypatch (brand_name kw)
    ctx = _ctx()
    upd = _update()
    asyncio.run(bh.generate_broll_preview(
        upd, ctx, claude=object(), theme="тест",
        chat_id=42, notion_url="http://x", notion_card_fn=None,
    ))
    _assert("broll_draft_id" in ctx.user_data, "draft_id положен в user_data", errors)
    # последнее отправленное сообщение = гейт
    last = ctx.bot.sends[-1] if ctx.bot.sends else {}
    cbs = _cbs(last.get("reply_markup"))
    _assert(any(str(c).startswith("b2scr:edit:") for c in cbs), f"гейт с Править: {cbs}", errors)
    _assert(any(str(c).startswith("b2scr:ok:") for c in cbs), "гейт с Утвердить", errors)
    _assert(not any(str(c).startswith("b2src:") for c in cbs),
            "меню источника НЕ показано сразу (гейт его предваряет)", errors)


def test_edit_enters_state_and_prompts(errors):
    print("\n[start_broll_script_edit — состояние broll2_edit_script + промпт без 'слов']")
    bh._bot_pending = {}
    bh._bot_save_pending = lambda *a, **k: None
    did = _seed_draft()
    ctx = _ctx()
    upd = _update()
    asyncio.run(bh.start_broll_script_edit(upd, ctx, did))
    st = bh._bot_pending.get(7, {})
    _assert(st.get("state") == "broll2_edit_script", f"состояние выставлено: {st}", errors)
    _assert(st.get("broll_edit_draft_id") == did, "draft_id сохранён для message-хендлера", errors)
    prompt = " ".join(s["text"] for s in ctx.bot.sends)
    _assert(len(ctx.bot.sends) >= 1, "промпт отправлен", errors)
    _assert("слов" not in prompt.lower(), "в промпте НЕТ selfie-валидации 'количество слов'", errors)


def test_edit_message_persists_any_length_no_warning(errors):
    print("\n[handle_script_edit_message — любой объём, без предупреждения]")
    bh._bot_pending = {7: {"state": "broll2_edit_script", "broll_edit_draft_id": _seed_draft(script="один два три")}}
    bh._bot_save_pending = lambda *a, **k: None
    new_text = "совершенно другой сценарий заметно большего размера на десяток слов точно"
    ctx = _ctx()
    upd = _update()
    upd.message = SimpleNamespace(text=new_text)
    handled = asyncio.run(bh.handle_script_edit_message(upd, ctx))
    _assert(handled is True, "сообщение обработано (return True)", errors)
    did = "broll_7_1700000000000"
    draft = load_draft(did, bh.DRAFTS_DIR)
    _assert(draft is not None and draft.script_text == new_text,
            "новый текст записан в durable draft.script_text", errors)
    _assert(bh._bot_pending.get(7, {}).get("state") != "broll2_edit_script",
            "состояние правки очищено", errors)
    all_text = " ".join(s["text"] for s in ctx.bot.sends).lower()
    _assert("количество слов" not in all_text and "орфограф" not in all_text,
            "НЕТ предупреждения о количестве слов (B-roll = свободный текст)", errors)
    cbs = [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(any(str(c).startswith("b2scr:ok:") for c in cbs), "после правки снова показан гейт", errors)


def test_approve_advances_to_source_menu(errors):
    print("\n[approve_broll_script — после утверждения появляется меню источника]")
    did = _seed_draft()
    ctx = _ctx()
    upd = _update()
    asyncio.run(bh.approve_broll_script(upd, ctx, did))
    cbs = [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(any(str(c).startswith("b2src:") for c in cbs), f"меню источника (b2src) показано: {cbs}", errors)
    _assert(any("auto" in str(c) for c in cbs), "режим Авто в меню источника", errors)


def test_unchanged_edit_no_warning(errors):
    print("\n[handle_script_edit_message — неизменённый текст → гейт без предупреждения]")
    same = "один два три"
    bh._bot_pending = {7: {"state": "broll2_edit_script", "broll_edit_draft_id": _seed_draft(script=same)}}
    bh._bot_save_pending = lambda *a, **k: None
    ctx = _ctx()
    upd = _update()
    upd.message = SimpleNamespace(text="  один два три  ")  # тот же по словам
    handled = asyncio.run(bh.handle_script_edit_message(upd, ctx))
    _assert(handled is True, "обработано", errors)
    all_text = " ".join(s["text"] for s in ctx.bot.sends).lower()
    _assert("количество слов" not in all_text, "без предупреждения на неизменённом тексте", errors)
    cbs = [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(any(str(c).startswith("b2scr:ok:") for c in cbs), "снова показан гейт", errors)


def main():
    errors = []
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_drafts_test_"))
    test_gate_keyboard(errors)
    test_generate_preview_shows_gate_not_source(errors)
    test_edit_enters_state_and_prompts(errors)
    test_edit_message_persists_any_length_no_warning(errors)
    test_approve_advances_to_source_menu(errors)
    test_unchanged_edit_no_warning(errors)
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
