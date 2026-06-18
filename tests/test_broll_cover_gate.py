"""TDD: B-roll Pipeline 2 — гейт 4а обложки (кадр из ролика + текст) — инкремент 4.

Артём: довести Pipeline 2 до паритета с селфи. Гейт 4 (обложка) — ПЕРВЫЙ
пост-сборочный шаг: монтаж рождается в конце, обложка-из-кадра требует готового
видео. По CTO-ревью (CONDITIONAL GO) + фильтр под пилот:
- persist монтажа per-user (broll_finals/{uid}.mp4), атомарно, удаляется после обложки;
- draft_id во всех b2cov-коллбэках + валидация (кнопка на финале живёт долго → защита от устаревшей);
- generate_cover ОБЯЗАН получать выбранный путь (avatar_override=None → случайный портрет Максима);
- Notion-патч best-effort (не валить выдачу обложки);
- движок cover.py (extract_frame/get_frame_timestamps/probe) и generate_cover — реюз как есть.

4а = кадр+текст (источники upload/библиотека — в 4b). Запуск:
python tests/test_broll_cover_gate.py
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
    def __init__(self):
        self.deleted = False; self.edits = []
    async def delete(self): self.deleted = True
    async def edit_text(self, text, **kw): self.edits.append(text)


class _FakeBot:
    def __init__(self):
        self.sends = []; self.videos = []; self.photos = []
    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup}); return _FakeMsg()
    async def send_video(self, chat_id, video=None, reply_markup=None, **kw):
        self.videos.append({"chat_id": chat_id}); return _FakeMsg()
    async def send_photo(self, chat_id, photo=None, reply_markup=None, **kw):
        self.photos.append({"chat_id": chat_id, "reply_markup": reply_markup}); return _FakeMsg()


DID = "broll_7_1700000000000"


def _ctx(broll_draft, draft_id=DID):
    return SimpleNamespace(bot=_FakeBot(), user_data={"broll_draft": broll_draft, "broll_draft_id": draft_id})


def _update(uid=7, chat=42):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(id=chat),
        callback_query=SimpleNamespace(message=SimpleNamespace(chat_id=chat)),
        message=None,
    )


def _draft(chat=42, **extra):
    d = {"script": "Сценарий про глэмпинг.", "clips": ["/tmp/x.mp4"], "theme": "тест",
         "notion_url": None, "notion_page_id": None, "chat_id": chat}
    d.update(extra); return d


class _FakeCover:
    """generate_cover-двойник: пишет dummy jpg, запоминает avatar_override (никогда None)."""
    def __init__(self): self.calls = []
    def __call__(self, cover_text, output_path, avatar_override=None):
        self.calls.append({"text": cover_text, "avatar_override": avatar_override})
        Path(output_path).write_bytes(b"\xff\xd8\xff" + b"\x00" * 2048); return output_path


def _make_video(p):
    Path(p).write_bytes(b"\x00" * 4096)


# ── Тесты ────────────────────────────────────────────────────────────

def test_assemble_persists_and_shows_cover_button(errors):
    print("\n[assemble — persist монтажа + кнопка обложки + не схлопывает черновик]")

    def _fake_montage(clip_paths, voiceover_path, output_path, tmp_dir=None, music_path=None, **kwargs):
        Path(output_path).write_bytes(b"\x00" * 4096)

    def _fake_voiceover(text, out_path, *a, **k):
        Path(out_path).write_bytes(b"\x00" * 2048)

    orig = bh.assemble_broll_montage
    bh.assemble_broll_montage = _fake_montage
    try:
        ctx = _ctx(_draft())
        asyncio.run(bh.assemble_broll_from_draft(_update(), ctx, _fake_voiceover, chat_id=42, status_fn=None))
    finally:
        bh.assemble_broll_montage = orig

    d = ctx.user_data.get("broll_draft")
    _assert(d is not None, "черновик НЕ схлопнут после сборки (нужен для обложки)", errors)
    _assert(d and d.get("stage") == "assembled", f"stage=assembled: {d and d.get('stage')}", errors)
    fp = d and d.get("final_path")
    _assert(fp and Path(fp).is_file(), f"монтаж сохранён per-user: {fp}", errors)
    _assert(d and d.get("draft_id") == DID, "draft_id записан в черновик", errors)
    last = ctx.bot.sends[-1] if ctx.bot.sends else {}
    cbs = _cbs(last.get("reply_markup"))
    _assert(f"b2cov:start:{DID}" in cbs, f"кнопка обложки с draft_id на финале: {cbs}", errors)
    _assert(len(ctx.bot.videos) == 1, "видео отправлено", errors)


def test_cover_picker_keyboard(errors):
    print("\n[_cover_picker_keyboard — кадры + skip + реюз broll_cancel]")
    cbs = _cbs(bh._cover_picker_keyboard(DID))
    _assert(f"b2cov:frame:{DID}:mid" in cbs or f"b2cov:frame:mid:{DID}" in cbs, f"кадр-кнопки: {cbs}", errors)
    _assert(any(str(c).startswith("b2cov:skip") for c in cbs), "skip (первый кадр)", errors)
    _assert("broll_cancel" in cbs, "реюз broll_cancel", errors)


def test_cover_start_validates_draft_id(errors):
    print("\n[b2cov:start — валидация draft_id (защита от устаревшей кнопки)]")
    # неверный id → НЕ показываем пикер
    ctx = _ctx(_draft(draft_id=DID, stage="assembled", final_path="/tmp/x"), draft_id=DID)
    ctx.user_data["broll_draft"]["draft_id"] = DID
    asyncio.run(bh.start_broll_cover_pick(_update(), ctx, "broll_7_OLD999", chat_id=42))
    cbs = [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(not any(str(c).startswith("b2cov:frame") for c in cbs), "устаревший id → пикер НЕ показан", errors)
    stale_txt = " ".join(s["text"] for s in ctx.bot.sends).lower()
    _assert("заново" in stale_txt or "устар" in stale_txt or "прошл" in stale_txt, "сообщение про устаревшую кнопку", errors)
    # верный id → пикер
    ctx2 = _ctx(_draft(draft_id=DID, stage="assembled", final_path="/tmp/x"), draft_id=DID)
    ctx2.user_data["broll_draft"]["draft_id"] = DID
    asyncio.run(bh.start_broll_cover_pick(_update(), ctx2, DID, chat_id=42))
    cbs2 = [c for s in ctx2.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert(any(str(c).startswith("b2cov:frame") for c in cbs2), "верный id → пикер показан", errors)


def test_frame_extract_and_confirm(errors):
    print("\n[b2cov:frame — извлечь кадр + превью с подтверждением]")
    tmp_final = Path(bh.DRAFTS_DIR) / "vid.mp4"; _make_video(tmp_final)
    bh.probe_video_duration = lambda p: 30.0
    bh.extract_frame = lambda v, ts, out: (Path(out).write_bytes(b"\xff\xd8\xff" + b"\x00" * 2048) or True)
    ctx = _ctx(_draft(draft_id=DID, stage="assembled", final_path=str(tmp_final)))
    ctx.user_data["broll_draft"]["draft_id"] = DID
    asyncio.run(bh.handle_broll_cover_cb(_update(), ctx, "frame", DID, arg="mid", chat_id=42))
    _assert(len(ctx.bot.photos) == 1, "превью кадра отправлено (send_photo)", errors)
    cbs = _cbs(ctx.bot.photos[-1]["reply_markup"])
    _assert(any(str(c).startswith("b2cov:confirm") for c in cbs) and any(str(c).startswith("b2cov:reject") for c in cbs),
            f"подтверждение/переснять: {cbs}", errors)
    _assert(ctx.user_data["broll_draft"].get("cover_image"), "путь кадра сохранён в черновик", errors)


def test_txt_off_renders_bare_and_finalizes(errors):
    print("\n[b2cov:txt:off — рендер без текста + финализация (cover_fn, publish, notion best-effort)]")
    cover = _FakeCover(); published = {}; patched = {}
    fp_final = Path(bh.DRAFTS_DIR) / f"broll_finals/7.mp4"; fp_final.parent.mkdir(parents=True, exist_ok=True); _make_video(fp_final)
    img = Path(bh.DRAFTS_DIR) / "frame.jpg"; img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 2048)
    ctx = _ctx(_draft(draft_id=DID, stage="assembled", final_path=str(fp_final),
                      cover_image=str(img), notion_page_id="pg123"))
    ctx.user_data["broll_draft"]["draft_id"] = DID
    asyncio.run(bh.handle_broll_cover_cb(
        _update(), ctx, "txt", DID, arg="off", chat_id=42,
        cover_fn=cover,
        publish_fn=lambda path, name: published.setdefault("url", "http://m/c.jpg") or "http://m/c.jpg",
        notion_cover_fn=lambda page_id, url: patched.update({"page": page_id, "url": url}),
    ))
    _assert(len(cover.calls) == 1, f"обложка отрендерена 1 раз: {len(cover.calls)}", errors)
    _assert(cover.calls and cover.calls[0]["avatar_override"] == str(img),
            "generate_cover получил ВЫБРАННЫЙ путь (не None — не случайный портрет!)", errors)
    _assert(cover.calls and cover.calls[0]["text"] == "", "без текста → пустой cover_text", errors)
    _assert(published.get("url"), "обложка опубликована (save_media_permanent)", errors)
    _assert(patched.get("page") == "pg123", "Notion-карточка пропатчена обложкой", errors)
    _assert(len(ctx.bot.photos) >= 1, "обложка отправлена пользователю", errors)
    _assert(not fp_final.exists(), "монтаж broll_finals очищен после обложки (не течёт диск)", errors)


def test_notion_fail_does_not_block_delivery(errors):
    print("\n[Notion-патч падает → обложка всё равно выдана (best-effort)]")
    cover = _FakeCover()
    img = Path(bh.DRAFTS_DIR) / "frame2.jpg"; img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 2048)
    ctx = _ctx(_draft(draft_id=DID, stage="assembled", final_path="/tmp/none.mp4",
                      cover_image=str(img), notion_page_id="pg123"))
    ctx.user_data["broll_draft"]["draft_id"] = DID

    def _boom(page_id, url): raise RuntimeError("notion down")
    asyncio.run(bh.handle_broll_cover_cb(
        _update(), ctx, "txt", DID, arg="off", chat_id=42,
        cover_fn=cover, publish_fn=lambda p, n: "http://m/c.jpg", notion_cover_fn=_boom,
    ))
    _assert(len(ctx.bot.photos) >= 1, "обложка выдана несмотря на падение Notion", errors)


def test_bot_wiring_present(errors):
    print("\n[bot.py — кнопка обложки + ветка b2cov]")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    _assert("start_broll_cover_pick" in src, "финал зовёт start_broll_cover_pick", errors)
    _assert('"b2cov:"' in src or "'b2cov:'" in src, "ветка b2cov в handle_callback", errors)


def main():
    errors = []
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_drafts_test_"))
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    test_assemble_persists_and_shows_cover_button(errors)
    test_cover_picker_keyboard(errors)
    test_cover_start_validates_draft_id(errors)
    test_frame_extract_and_confirm(errors)
    test_txt_off_renders_bare_and_finalizes(errors)
    test_notion_fail_does_not_block_delivery(errors)
    test_bot_wiring_present(errors)
    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors: print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
