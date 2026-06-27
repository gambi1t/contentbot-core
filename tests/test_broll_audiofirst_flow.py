"""TDD: Fix #5 — проводка audio-first в ветке AI_VIDEO_GO (handle_broll_source).

На «🚀 Запустить» AI-видео: озвучка-черновик ПЕРВОЙ → ffprobe реальной длины →
generate_ai_broll(clip_durations=микс 5/10). Фолбэк на оценку слов, если
озвучка не вышла / voiceover_fn не прокинут.

Запуск: python -m pytest tests/test_broll_audiofirst_flow.py -v
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
import broll.assembler as basm  # noqa: E402
import broll.draft as bd  # noqa: E402
import ai_video_broll  # noqa: E402
from broll.draft import BrollDraft, save_draft, SourceMode  # noqa: E402

bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_af_"))


class _FakeMsg:
    async def delete(self):
        pass

    async def edit_text(self, *a, **k):
        pass


class _FakeBot:
    def __init__(self):
        self.texts = []
        self.audios = 0

    async def send_message(self, chat_id, text=None, **kw):
        self.texts.append(text)
        return _FakeMsg()

    async def send_audio(self, chat_id, audio=None, **kw):
        self.audios += 1
        return _FakeMsg()


def _ctx():
    return SimpleNamespace(bot=_FakeBot(), user_data={})


def _update(uid=7, chat=42):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(id=chat),
        callback_query=SimpleNamespace(
            message=SimpleNamespace(chat_id=chat), from_user=SimpleNamespace(id=uid)),
        message=None,
    )


def _make_draft(did="broll_7_af"):
    d = BrollDraft(
        draft_id=did, user_id=7, chat_id=42, status="preview_ready",
        source_mode=None, script_text="Сценарий про картинг и драйв в Тюмени.",
        voice_estimate_sec=0.0,
    )
    save_draft(d, bh.DRAFTS_DIR)
    return did


def _common_patches(monkeypatch, captured):
    def _fake_generate(script, out_dir, claude=None, duration=5, progress_cb=None,
                       max_clips=12, target_clips=None, business_context=None,
                       clip_durations=None):
        captured["clip_durations"] = clip_durations
        n = len(clip_durations) if clip_durations else (target_clips or 2)
        return [Path(out_dir) / f"ai_{i:02d}.mp4" for i in range(1, n + 1)], 0.0

    monkeypatch.setattr(ai_video_broll, "generate_ai_broll", _fake_generate)
    # hf_items_from_clips — реальный (оборачивает пути в BrollItem для save_draft).
    monkeypatch.setattr(bh, "materialize_items", lambda *a, **k: None)

    async def _noop_preview(*a, **k):
        return None

    monkeypatch.setattr(bh, "_send_hf_preview", _noop_preview)


def test_go_sizes_clips_from_voiceover(monkeypatch):
    captured = {}
    _common_patches(monkeypatch, captured)
    # озвучка-черновик пишет mp3; probe возвращает 14.6с → план [10,5]
    monkeypatch.setattr(basm, "_probe_duration", lambda p: 14.6)

    def _fake_voice(script, out_path):
        Path(out_path).write_bytes(b"\x00" * 4096)

    did = _make_draft()
    asyncio.run(bh.handle_broll_source(
        _update(), _ctx(), None, did, SourceMode.AI_VIDEO_GO, voiceover_fn=_fake_voice))
    assert captured.get("clip_durations") == [10, 5], (
        f"клипы размечены под озвучку 14.6с: {captured.get('clip_durations')}"
    )


def test_go_fallback_to_wordcount_without_voiceover(monkeypatch):
    captured = {}
    _common_patches(monkeypatch, captured)
    did = _make_draft("broll_7_af2")
    # voiceover_fn=None → фолбэк на оценку слов (равные длины), но всё равно
    # передаём clip_durations (план из fullscreen_plan).
    asyncio.run(bh.handle_broll_source(
        _update(), _ctx(), None, did, SourceMode.AI_VIDEO_GO, voiceover_fn=None))
    durs = captured.get("clip_durations")
    assert durs and all(d == durs[0] for d in durs), (
        f"фолбэк: равные длины из оценки слов: {durs}"
    )


def test_go_refuses_when_plan_undercovers_voiceover(monkeypatch):
    # C3: озвучка 200с > cap 60 → план обрежется до 60 < 200 → отказ ДО оплаты Kling.
    captured = {}
    _common_patches(monkeypatch, captured)
    monkeypatch.setattr(basm, "_probe_duration", lambda p: 200.0)

    def _fake_voice(script, out_path):
        Path(out_path).write_bytes(b"\x00" * 4096)

    did = _make_draft("broll_7_undercover")
    ctx = _ctx()
    asyncio.run(bh.handle_broll_source(
        _update(), ctx, None, did, SourceMode.AI_VIDEO_GO, voiceover_fn=_fake_voice))
    assert "clip_durations" not in captured, "generate_ai_broll НЕ должен вызываться (не платим за недопокрытие)"
    txt = " ".join(t or "" for t in ctx.bot.texts).lower()
    assert "длиннее" in txt or "лимит" in txt or "сократи" in txt, f"нет понятного отказа юзеру: {txt}"


def test_go_fallback_when_probe_fails(monkeypatch):
    captured = {}
    _common_patches(monkeypatch, captured)
    monkeypatch.setattr(basm, "_probe_duration", lambda p: 0.0)  # probe вернул 0 → фолбэк

    def _fake_voice(script, out_path):
        Path(out_path).write_bytes(b"\x00" * 4096)

    did = _make_draft("broll_7_af3")
    asyncio.run(bh.handle_broll_source(
        _update(), _ctx(), None, did, SourceMode.AI_VIDEO_GO, voiceover_fn=_fake_voice))
    assert captured.get("clip_durations"), "при сбое probe всё равно передаём план (фолбэк)"


# ── Реюз озвучки из GO в preview (нет дрейфа длины + второго TTS) ─────

def _seed_voice(uid, script):
    mp3 = bh.DRAFTS_DIR.parent / "broll_voice" / f"aivoice_{uid}.mp3"
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(b"\x00" * 4096)
    mp3.with_suffix(".script.txt").write_text(script, encoding="utf-8")
    return mp3


def _voice_ctx(script):
    return SimpleNamespace(bot=_FakeBot(),
                           user_data={"broll_draft": {"script": script, "chat_id": 42}})


def test_preview_reuses_go_voiceover():
    _seed_voice(7, "СЦЕНАРИЙ ПРО КАРТИНГ И ДРАЙВ")
    gen = {"n": 0}

    def _voice(script, out):
        gen["n"] += 1
        Path(out).write_bytes(b"\x00" * 4096)

    ctx = _voice_ctx("СЦЕНАРИЙ ПРО КАРТИНГ И ДРАЙВ")
    asyncio.run(bh.preview_broll_voiceover(_update(), ctx, _voice, chat_id=42, status_fn=None))
    assert gen["n"] == 0, "озвучка из GO должна переиспользоваться (маркер совпал), без второго TTS"
    assert ctx.bot.audios == 1, "аудио-превью всё равно отправлено"


def test_preview_regenerates_when_script_differs():
    _seed_voice(7, "СТАРЫЙ СЦЕНАРИЙ")
    gen = {"n": 0}

    def _voice(script, out):
        gen["n"] += 1
        Path(out).write_bytes(b"\x00" * 4096)

    ctx = _voice_ctx("НОВЫЙ СОВСЕМ ДРУГОЙ СЦЕНАРИЙ")
    asyncio.run(bh.preview_broll_voiceover(_update(), ctx, _voice, chat_id=42, status_fn=None))
    assert gen["n"] == 1, "при несовпадении сценария — генерим заново (нет ложного реюза)"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
