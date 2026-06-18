"""TDD: B-roll Pipeline 2 — гейт 4b обложки: источники загрузка + библиотека.

4a дал кадр-из-ролика + текст. 4b добавляет (аддитивно, та же финализация):
- 📤 Загрузить фото (b2cov:upload → state broll2_cover_upload → приём фото-сообщения);
- 📚 Из библиотеки (b2cov:library → грид cover.list_library_sample → lib_pick/lib_reroll/back).
Реюз cover.py (list_library_sample/lookup_library_path) как есть; upload-перехват —
явным pending-состоянием (как broll2_edit_script/cover_text), без конфликта с b2up.

Запуск: python tests/test_broll_cover_sources.py
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


class _FakeFile:
    async def download_to_drive(self, path):
        Path(path).write_bytes(b"\xff\xd8\xff" + b"\x00" * 2048)


class _FakeMsg:
    async def delete(self): pass
    async def edit_text(self, *a, **k): pass


class _FakeBot:
    def __init__(self):
        self.sends = []; self.photos = []
    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sends.append({"text": text, "reply_markup": reply_markup}); return _FakeMsg()
    async def send_photo(self, chat_id, photo=None, reply_markup=None, **kw):
        self.photos.append({"reply_markup": reply_markup}); return _FakeMsg()
    async def get_file(self, file_id):
        return _FakeFile()


DID = "broll_7_1700000000000"


def _ctx(broll_draft):
    return SimpleNamespace(bot=_FakeBot(), user_data={"broll_draft": broll_draft, "broll_draft_id": DID})


def _update(uid=7, chat=42, photo=None, document=None):
    msg = SimpleNamespace(photo=photo or [], document=document, text=None) if (photo or document) else None
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(id=chat),
        callback_query=SimpleNamespace(message=SimpleNamespace(chat_id=chat)),
        message=msg,
    )


def _draft(**extra):
    d = {"script": "s", "clips": ["/tmp/x.mp4"], "theme": "t", "notion_url": None,
         "notion_page_id": None, "chat_id": 42, "draft_id": DID, "stage": "assembled",
         "final_path": "/tmp/final.mp4"}
    d.update(extra); return d


# ── Тесты ────────────────────────────────────────────────────────────

def test_picker_has_upload_and_library(errors):
    print("\n[пикер 4b — добавлены загрузка + библиотека]")
    cbs = _cbs(bh._cover_picker_keyboard(DID))
    _assert(f"b2cov:upload:{DID}" in cbs, f"кнопка загрузки: {cbs}", errors)
    _assert(f"b2cov:library:{DID}" in cbs, "кнопка библиотеки", errors)


def test_upload_sets_state_and_prompts(errors):
    print("\n[b2cov:upload — состояние приёма фото + промпт]")
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    ctx = _ctx(_draft())
    asyncio.run(bh.handle_broll_cover_cb(_update(), ctx, "upload", DID, chat_id=42))
    st = bh._bot_pending.get(7, {})
    _assert(st.get("state") == "broll2_cover_upload", f"состояние upload: {st}", errors)
    _assert(len(ctx.bot.sends) >= 1, "промпт прислать фото отправлен", errors)


def test_cover_photo_message_accepts_and_goes_to_text(errors):
    print("\n[handle_broll_cover_photo — приём фото → cover_image → выбор текста]")
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_cov_"))
    bh._bot_pending = {7: {"state": "broll2_cover_upload", "cover_draft_id": DID}}
    bh._bot_save_pending = lambda *a, **k: None
    ctx = _ctx(_draft())
    upd = _update(photo=[SimpleNamespace(file_id="ph1")])
    handled = asyncio.run(bh.handle_broll_cover_photo(upd, ctx))
    _assert(handled is True, "фото обработано (return True)", errors)
    img = ctx.user_data["broll_draft"].get("cover_image")
    _assert(img and Path(img).is_file(), f"фото скачано в cover_image: {img}", errors)
    _assert(bh._bot_pending.get(7, {}).get("state") != "broll2_cover_upload", "состояние upload снято", errors)
    cbs = [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(any(str(c).startswith("b2cov:txt") for c in cbs), f"переход к выбору текста: {cbs}", errors)


def test_cover_photo_rejects_non_image(errors):
    print("\n[handle_broll_cover_photo — не картинка → отказ, без cover_image]")
    bh._bot_pending = {7: {"state": "broll2_cover_upload", "cover_draft_id": DID}}
    bh._bot_save_pending = lambda *a, **k: None
    ctx = _ctx(_draft())
    upd = _update()
    upd.message = SimpleNamespace(photo=[], document=SimpleNamespace(file_id="d1", mime_type="application/pdf"), text=None)
    handled = asyncio.run(bh.handle_broll_cover_photo(upd, ctx))
    _assert(handled is True, "обработано (перехвачено состоянием)", errors)
    _assert(not ctx.user_data["broll_draft"].get("cover_image"), "cover_image НЕ установлен на не-картинке", errors)


def test_library_grid_and_pick(errors):
    print("\n[b2cov:library — грид + lib_pick → cover_image → текст]")
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_cov2_"))
    libdir = bh.DRAFTS_DIR / "lib"; libdir.mkdir(parents=True, exist_ok=True)
    f1 = libdir / "ph_a.jpg"; f1.write_bytes(b"\xff\xd8\xff" + b"\x00" * 2048)
    f2 = libdir / "ph_b.jpg"; f2.write_bytes(b"\xff\xd8\xff" + b"\x00" * 2048)
    bh.list_library_sample = lambda n=6, exclude_ids=None: [
        {"id": "ph_a", "path": str(f1)}, {"id": "ph_b", "path": str(f2)}]
    bh.lookup_library_path = lambda pid: str(f1) if pid == "ph_a" else None
    ctx = _ctx(_draft())
    asyncio.run(bh.handle_broll_cover_cb(_update(), ctx, "library", DID, chat_id=42))
    _assert(len(ctx.bot.photos) >= 2, f"превью библиотеки отправлены: {len(ctx.bot.photos)}", errors)
    all_cbs = [c for p in ctx.bot.photos for c in _cbs(p.get("reply_markup"))] + \
              [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(any(str(c).startswith("b2cov:lib_pick:") for c in all_cbs), f"кнопки выбора фото: {all_cbs}", errors)
    _assert(any(str(c).startswith("b2cov:lib_reroll") for c in all_cbs), "кнопка «ещё»", errors)
    # выбор фото
    ctx2 = _ctx(_draft())
    asyncio.run(bh.handle_broll_cover_cb(_update(), ctx2, "lib_pick", DID, arg="ph_a", chat_id=42))
    _assert(ctx2.user_data["broll_draft"].get("cover_image") == str(f1), "lib_pick → cover_image из библиотеки", errors)
    cbs2 = [c for s in ctx2.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(any(str(c).startswith("b2cov:txt") for c in cbs2), "после выбора → текст", errors)


def test_bot_wiring_cover_upload_route(errors):
    print("\n[bot.py — message-route broll2_cover_upload]")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    _assert("broll2_cover_upload" in src, "роут состояния broll2_cover_upload", errors)
    _assert("handle_broll_cover_photo" in src, "вызов handle_broll_cover_photo", errors)


def main():
    errors = []
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_cov0_"))
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    test_picker_has_upload_and_library(errors)
    test_upload_sets_state_and_prompts(errors)
    test_cover_photo_message_accepts_and_goes_to_text(errors)
    test_cover_photo_rejects_non_image(errors)
    test_library_grid_and_pick(errors)
    test_bot_wiring_cover_upload_route(errors)
    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors: print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
