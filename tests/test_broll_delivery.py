"""TDD: B-roll Pipeline 2 — безопасная финальная доставка (фикс 413-краша).

Баг: assemble_broll_from_draft отдавал финал голым send_video → 413 на >50 МБ
ронял всю сборку (нет ни ролика, ни финал-экрана). По CTO-ревью (CONDITIONAL GO)
под пилот:
- _broll_deliver(bot, chat_id, path, caption) → preflight ≤48 МБ → send_document
  (макс. качество, без транскода); >48 МБ ИЛИ фейл → nginx-ссылка; полный фейл → None;
- assemble зовёт через DI deliver_fn; Notion «Готово» только если доставка прошла;
- легаси-ветка (deliver_fn=None) обёрнута в try/except (без латентного 413-краша).
Реюз: send_document+save_media_permanent лесенка карточного пути (bot.py:14406);
лимит 48 МБ как MAX_BOT_UPLOAD. Файл переживает rmtree (broll_finals/{uid}.mp4 от гейта 4).

Запуск: python tests/test_broll_delivery.py
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

import bot  # noqa: E402
import broll.handlers as bh  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _file(path, size_bytes):
    """Разреженный файл нужного размера (без реальных байт — мгновенно)."""
    with open(path, "wb") as f:
        if size_bytes > 0:
            f.truncate(size_bytes)
    return str(path)


class _FakeMsg:
    async def delete(self): pass
    async def edit_text(self, *a, **k): pass


class _DeliverBot:
    """Фейк bot для _broll_deliver: send_document может падать, send_message пишет.
    Пишет последние kwargs (reply_markup/filename/parse_mode) для проверки проброса."""
    def __init__(self, doc_raises=False):
        self.doc_calls = 0; self.msg_calls = []; self._doc_raises = doc_raises
        self.last_doc_kw = {}; self.last_msg_kw = {}
    async def send_document(self, chat_id, document=None, caption=None, **kw):
        self.doc_calls += 1; self.last_doc_kw = kw
        if self._doc_raises:
            raise Exception("Request Entity Too Large (413)")
        return SimpleNamespace(document=SimpleNamespace(file_id="doc1"))
    async def send_message(self, chat_id, text=None, **kw):
        self.msg_calls.append(text); self.last_msg_kw = kw; return _FakeMsg()


SMALL = 10 * 1024 * 1024     # 10 МБ ≤ 48
BIG = 60 * 1024 * 1024       # 60 МБ > 48


# ── _broll_deliver: лесенка доставки ─────────────────────────────────

def test_deliver_small_document(errors):
    print("\n[_broll_deliver — ≤48 МБ → документ, без ссылки]")
    tmp = Path(tempfile.mkdtemp()); p = _file(tmp / "v.mp4", SMALL)
    bot.save_media_permanent = lambda path, prefix="file": (_ for _ in ()).throw(AssertionError("ссылка не должна вызываться"))
    fb = _DeliverBot()
    kind = asyncio.run(bot._broll_deliver(fb, 42, p, "cap"))
    _assert(kind == "document", f"вернул 'document': {kind}", errors)
    _assert(fb.doc_calls == 1, "send_document вызван 1 раз", errors)


def test_deliver_big_preflight_link(errors):
    print("\n[_broll_deliver — >48 МБ → СРАЗУ ссылка (preflight, без send_document)]")
    tmp = Path(tempfile.mkdtemp()); p = _file(tmp / "v.mp4", BIG)
    published = {}
    bot.save_media_permanent = lambda path, prefix="file": published.setdefault("url", "http://m/b.mp4") or "http://m/b.mp4"
    fb = _DeliverBot()
    kind = asyncio.run(bot._broll_deliver(fb, 42, p, "cap"))
    _assert(kind == "link", f"вернул 'link': {kind}", errors)
    _assert(fb.doc_calls == 0, "send_document НЕ вызывался (preflight по размеру)", errors)
    _assert(published.get("url"), "save_media_permanent вызван", errors)
    _assert(any("http://m/b.mp4" in (m or "") for m in fb.msg_calls), "ссылка отправлена сообщением", errors)


def test_deliver_413_fallback_link(errors):
    print("\n[_broll_deliver — send_document падает 413 → fallback ссылка]")
    tmp = Path(tempfile.mkdtemp()); p = _file(tmp / "v.mp4", SMALL)
    bot.save_media_permanent = lambda path, prefix="file": "http://m/c.mp4"
    fb = _DeliverBot(doc_raises=True)
    kind = asyncio.run(bot._broll_deliver(fb, 42, p, "cap"))
    _assert(fb.doc_calls == 1, "send_document попробован", errors)
    _assert(kind == "link", f"после 413 → ссылка: {kind}", errors)


def test_deliver_total_fail_none(errors):
    print("\n[_broll_deliver — и документ, и ссылка падают → None, без исключения]")
    tmp = Path(tempfile.mkdtemp()); p = _file(tmp / "v.mp4", SMALL)
    bot.save_media_permanent = lambda path, prefix="file": (_ for _ in ()).throw(Exception("disk full"))
    fb = _DeliverBot(doc_raises=True)
    kind = asyncio.run(bot._broll_deliver(fb, 42, p, "cap"))
    _assert(kind is None, f"полный фейл → None (не падаем): {kind}", errors)


# ── _broll_deliver: проброс reply_markup / filename (универсальный финал) ──

def test_deliver_reply_markup_on_document(errors):
    print("\n[_broll_deliver — reply_markup проброшен в send_document]")
    tmp = Path(tempfile.mkdtemp()); p = _file(tmp / "v.mp4", SMALL)
    bot.save_media_permanent = lambda path, prefix="file": "http://m/x.mp4"
    fb = _DeliverBot()
    KB = SimpleNamespace(inline_keyboard=[["btn"]])
    kind = asyncio.run(bot._broll_deliver(fb, 42, p, "cap", reply_markup=KB))
    _assert(kind == "document", f"документ: {kind}", errors)
    _assert(fb.last_doc_kw.get("reply_markup") is KB, "кнопки «что дальше» под документом", errors)


def test_deliver_reply_markup_on_link(errors):
    print("\n[_broll_deliver — reply_markup проброшен в ссылку-сообщение (>48 МБ)]")
    tmp = Path(tempfile.mkdtemp()); p = _file(tmp / "v.mp4", BIG)
    bot.save_media_permanent = lambda path, prefix="file": "http://m/x.mp4"
    fb = _DeliverBot()
    KB = SimpleNamespace(inline_keyboard=[["btn"]])
    kind = asyncio.run(bot._broll_deliver(fb, 42, p, "cap", reply_markup=KB))
    _assert(kind == "link", f"ссылка: {kind}", errors)
    _assert(fb.last_msg_kw.get("reply_markup") is KB, "кнопки под ссылкой-сообщением", errors)


def test_deliver_custom_filename(errors):
    print("\n[_broll_deliver — filename override (имя для скачивания)]")
    tmp = Path(tempfile.mkdtemp()); p = _file(tmp / "v.mp4", SMALL)
    bot.save_media_permanent = lambda path, prefix="file": "http://m/x.mp4"
    fb = _DeliverBot()
    asyncio.run(bot._broll_deliver(fb, 42, p, "cap", filename="Мой ролик.mp4"))
    _assert(fb.last_doc_kw.get("filename") == "Мой ролик.mp4", "имя файла переопределено", errors)


# ── assemble_broll_from_draft использует deliver_fn ──────────────────

class _AsmBot:
    def __init__(self, video_raises=False):
        self.sends = []; self.videos = 0; self._vr = video_raises
    async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
        self.sends.append(text); return _FakeMsg()
    async def send_video(self, chat_id, video=None, **kw):
        self.videos += 1
        if self._vr:
            raise Exception("Request Entity Too Large (413)")
        return _FakeMsg()


def _asm_ctx(draft):
    return SimpleNamespace(bot=_AsmBot(), user_data={"broll_draft": draft, "broll_draft_id": "broll_7_1"})


def _asm_ctx_video_raises(draft):
    c = _asm_ctx(draft); c.bot = _AsmBot(video_raises=True); return c


def _upd():
    return SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=42),
                           callback_query=SimpleNamespace(message=SimpleNamespace(chat_id=42)), message=None)


def _draft():
    return {"script": "s", "clips": ["/tmp/x.mp4"], "theme": "t", "notion_url": None,
            "notion_page_id": "pg1", "chat_id": 42}


def _setup_assemble_stubs():
    bh.assemble_broll_montage = lambda *a, **k: Path(a[2]).write_bytes(b"\x00" * 200)


def test_assemble_uses_deliver_fn_and_gates_notion(errors):
    print("\n[assemble — зовёт deliver_fn; Notion «Готово» только при успехе доставки]")
    _setup_assemble_stubs()
    def _vfn(text, out, *a, **k): Path(out).write_bytes(b"\x00" * 2048)
    status_calls = []
    # успех доставки → status_fn вызывается
    ctx = _asm_ctx(_draft())
    asyncio.run(bh.assemble_broll_from_draft(
        _upd(), ctx, _vfn, chat_id=42, status_fn=lambda pid, st: status_calls.append(st),
        deliver_fn=lambda cid, p, cap: _async_ret("document")))
    _assert("Готово к публикации" in status_calls, "доставка ok → Notion «Готово к публикации»", errors)
    _assert(ctx.bot.videos == 0, "send_video НЕ вызывался (используется deliver_fn)", errors)
    # полный фейл доставки → Notion НЕ помечаем «Готово»
    status_calls.clear()
    ctx2 = _asm_ctx(_draft())
    asyncio.run(bh.assemble_broll_from_draft(
        _upd(), ctx2, _vfn, chat_id=42, status_fn=lambda pid, st: status_calls.append(st),
        deliver_fn=lambda cid, p, cap: _async_ret(None)))
    _assert("Готово к публикации" not in status_calls, "доставка провалена → Notion НЕ «Готово»", errors)


def test_assemble_legacy_default_no_crash(errors):
    print("\n[assemble — deliver_fn=None + send_video падает 413 → НЕ крашится]")
    _setup_assemble_stubs()
    def _vfn(text, out, *a, **k): Path(out).write_bytes(b"\x00" * 2048)
    ctx = _asm_ctx_video_raises(_draft())
    crashed = False
    try:
        asyncio.run(bh.assemble_broll_from_draft(_upd(), ctx, _vfn, chat_id=42, status_fn=None))
    except Exception as e:
        crashed = True; print("   raised:", e)
    _assert(not crashed, "легаси send_video обёрнут — сборка не падает на 413", errors)


def _async_ret(val):
    async def _c(): return val
    return _c()


def test_bot_wiring_deliver(errors):
    print("\n[bot.py — deliver_fn проброшен в обоих голосовых форках]")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    _assert("_broll_deliver" in src, "_broll_deliver определён", errors)
    _assert(src.count("deliver_fn=") >= 2, "deliver_fn проброшен ≥2 раз (ИИ-голос + свой голос)", errors)


def test_montage_uses_canon_deliver(errors):
    print("\n[bot.py — монтаж (card_asm_go) отдаёт через канон _broll_deliver; "
          "мёртвой ffmpeg-компрессии нет]")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    # Мёртвый блок 413-компрессии монтажа удалён: ffmpeg жёг ~420с CPU, а
    # результат (send_file/_tg_compressed) нигде не использовался (отдавался
    # оригинал final_path). Канон _broll_deliver делает size-preflight сам.
    _assert("final_auto_tg.mp4" not in src, "мёртвая ffmpeg-компрессия монтажа удалена (final_auto_tg.mp4)", errors)
    _assert("_tg_compressed" not in src, "переменная _tg_compressed удалена", errors)
    # Финал монтажа зовёт канон. Якорь — подпись авто-ролика.
    anchor = src.find("✅ Авто-ролик готов")
    _assert(anchor != -1, "блок финала монтажа найден (подпись авто-ролика)", errors)
    if anchor != -1:
        region = src[anchor:anchor + 1400]
        _assert("_broll_deliver(" in region, "монтаж отдаёт через канон _broll_deliver", errors)


def main():
    errors = []
    test_deliver_small_document(errors)
    test_deliver_big_preflight_link(errors)
    test_deliver_413_fallback_link(errors)
    test_deliver_total_fail_none(errors)
    test_deliver_reply_markup_on_document(errors)
    test_deliver_reply_markup_on_link(errors)
    test_deliver_custom_filename(errors)
    test_assemble_uses_deliver_fn_and_gates_notion(errors)
    test_assemble_legacy_default_no_crash(errors)
    test_bot_wiring_deliver(errors)
    test_montage_uses_canon_deliver(errors)
    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors: print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
