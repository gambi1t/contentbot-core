"""TDD: Fix #4 — B-roll пишет ссылку на финал в карточку Notion.

Баг (запись SMM 25.06): готовый B-roll доставляется в чат и копируется в папку
проекта (мост), но ссылка-блок в карточку Notion НЕ добавляется (селфи/аватар —
добавляют). Фикс (Q3 reuse + DI): новый `notion_attach_fn(card_id, video_path)`
прокидывается из bot.py (где живут notion-клиент и save_media_permanent — иначе
циклический импорт), вызывается в `assemble_broll_from_draft` после
`bridge_broll_to_publication`. Покрывает все 6 источников (один choke point).

Запуск: python -m pytest tests/test_broll_notion_attach.py -v
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import broll.handlers as bh  # noqa: E402

bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_drafts_attach_"))
BOT_SRC = (ROOT / "bot.py").read_text(encoding="utf-8")


class _FakeMsg:
    async def delete(self):
        pass

    async def edit_text(self, *a, **k):
        pass


class _FakeBot:
    def __init__(self):
        self.videos = []

    async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
        return _FakeMsg()

    async def send_video(self, chat_id, video=None, **kw):
        self.videos.append(chat_id)
        return _FakeMsg()


def _ctx(draft):
    return SimpleNamespace(bot=_FakeBot(), user_data={"broll_draft": draft})


def _update(uid=7, chat=42):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(id=chat),
        callback_query=SimpleNamespace(message=SimpleNamespace(chat_id=chat)),
        message=None,
    )


def _draft(chat=42, **extra):
    d = {
        "script": "Тестовый сценарий про картинг.",
        "clips": ["/tmp/nonexistent_clip.mp4"],
        "theme": "тест", "notion_url": None, "notion_page_id": None, "chat_id": chat,
    }
    d.update(extra)
    return d


def _fake_montage(clip_paths, voiceover_path, output_path, tmp_dir=None, music_path=None, **kw):
    Path(output_path).write_bytes(b"\x00" * 200)


def _fake_voiceover(text, out_path, *a, **k):
    Path(out_path).write_bytes(b"\x00" * 2048)


def _patch_assemble_deps():
    """Глушим тяжёлые шаги (монтаж/мост/биллинг/субтитры), чтобы дойти до attach
    быстро и без ffmpeg/whisper."""
    import subtitle_burner

    async def _bridge(draft, uid=None, tg_post_fn=None):
        return False

    async def _charge(*a, **k):
        return None

    orig = (
        bh.assemble_broll_montage, bh.bridge_broll_to_publication,
        bh._charge_broll_publication, subtitle_burner.add_subtitles_to_video,
    )
    bh.assemble_broll_montage = _fake_montage
    bh.bridge_broll_to_publication = _bridge
    bh._charge_broll_publication = _charge
    subtitle_burner.add_subtitles_to_video = lambda montage, *a, **k: str(montage)
    return orig


def _restore_assemble_deps(orig):
    import subtitle_burner
    (bh.assemble_broll_montage, bh.bridge_broll_to_publication,
     bh._charge_broll_publication, subtitle_burner.add_subtitles_to_video) = orig


# ── Поведение assemble_broll_from_draft ──────────────────────────────

def test_attach_called_with_page_and_path():
    captured = {}

    async def _attach(card_id, video_path):
        captured["card_id"] = card_id
        captured["video_path"] = video_path

    orig = _patch_assemble_deps()
    try:
        ctx = _ctx(_draft(notion_page_id="PAGE123"))
        asyncio.run(bh.assemble_broll_from_draft(
            _update(), ctx, _fake_voiceover, chat_id=42, status_fn=None,
            notion_attach_fn=_attach))
        assert captured.get("card_id") == "PAGE123", f"attach получил page_id: {captured}"
        assert captured.get("video_path"), f"attach получил путь финала: {captured}"
    finally:
        _restore_assemble_deps(orig)


def test_no_attach_when_page_missing():
    captured = {}

    async def _attach(card_id, video_path):
        captured["called"] = True

    orig = _patch_assemble_deps()
    try:
        ctx = _ctx(_draft(notion_page_id=None))  # нет карточки → нет attach
        asyncio.run(bh.assemble_broll_from_draft(
            _update(), ctx, _fake_voiceover, chat_id=42, status_fn=None,
            notion_attach_fn=_attach))
        assert not captured.get("called"), "attach не должен вызываться без notion_page_id"
    finally:
        _restore_assemble_deps(orig)


def test_backward_compat_attach_fn_optional():
    # старые вызовы без notion_attach_fn (и существующие тесты) не должны падать
    orig = _patch_assemble_deps()
    try:
        ctx = _ctx(_draft(notion_page_id="PAGE123"))
        asyncio.run(bh.assemble_broll_from_draft(
            _update(), ctx, _fake_voiceover, chat_id=42, status_fn=None))  # без notion_attach_fn
    finally:
        _restore_assemble_deps(orig)


def test_attach_failure_does_not_block_delivery():
    # H3: сбой/таймаут Notion-attach не должен валить флоу — ролик уже доставлен.
    async def _attach(card_id, video_path):
        raise RuntimeError("notion down")

    orig = _patch_assemble_deps()
    try:
        ctx = _ctx(_draft(notion_page_id="PAGE123"))
        asyncio.run(bh.assemble_broll_from_draft(
            _update(), ctx, _fake_voiceover, chat_id=42, status_fn=None,
            notion_attach_fn=_attach))
        assert ctx.bot.videos, "ролик доставлен несмотря на сбой Notion-attach"
    finally:
        _restore_assemble_deps(orig)


def test_attach_wrapped_in_timeout():
    # H3: вызов attach обёрнут в asyncio.wait_for (не подвесит хвост).
    src = (ROOT / "broll" / "handlers.py").read_text(encoding="utf-8")
    import re as _re
    assert _re.search(r"wait_for\(\s*notion_attach_fn", src), "attach не обёрнут в wait_for(timeout)"


def test_accept_voiceover_forwards_attach():
    # accept_broll_voiceover (ai-голос) должен прокидывать notion_attach_fn в assemble
    forwarded = {}

    async def _fake_assemble(update, context, voiceover_fn, **kw):
        forwarded.update(kw)

    orig = bh.assemble_broll_from_draft
    bh.assemble_broll_from_draft = _fake_assemble
    try:
        mp3 = bh.DRAFTS_DIR / "ai_voice.mp3"
        mp3.write_bytes(b"\x00" * 2048)
        ctx = _ctx(_draft(ai_voice_path=str(mp3)))

        async def _attach(card_id, video_path):
            pass

        asyncio.run(bh.accept_broll_voiceover(
            _update(), ctx, chat_id=42, status_fn=None, notion_attach_fn=_attach))
        assert forwarded.get("notion_attach_fn") is _attach, (
            f"accept_broll_voiceover не прокинул notion_attach_fn: {list(forwarded)}"
        )
    finally:
        bh.assemble_broll_from_draft = orig


# ── Source-level: bot.py — helper + обе инъекции ─────────────────────

def test_bot_defines_attach_helper():
    assert "_broll_notion_attach" in BOT_SRC, "нет DI-хелпера _broll_notion_attach в bot.py"
    # хелпер обязан использовать эталонный паттерн селфи
    assert "save_media_permanent" in BOT_SRC and "blocks.children.append" in BOT_SRC, (
        "attach-хелпер не использует save_media_permanent + notion.blocks.children.append"
    )


def test_both_callsites_inject_attach():
    # own-voice (assemble) и b2vop:accept (accept_broll_voiceover) — оба прокидывают
    assert BOT_SRC.count("notion_attach_fn=_broll_notion_attach") >= 2, (
        "notion_attach_fn не прокинут в обоих местах инъекции"
    )


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
