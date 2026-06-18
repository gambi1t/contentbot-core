"""TDD: B-roll Pipeline 2 — гейт превью озвучки до монтажа (инкремент 2).

Артём (17 июня): довести Pipeline 2 до стандартного пайплайна — пошаговый
контроль. Гейт #2: после выбора «🤖 Голос Максима» бот генерит ИИ-озвучку
ОДИН раз, шлёт её аудио-превью с кнопками «✅ Собрать / 🔄 Перегенерировать»,
и тяжёлый ffmpeg-монтаж запускается ТОЛЬКО после accept — переиспользуя уже
сгенерённый mp3 (ElevenLabs второй раз не дёргается).

Реюз-паттерн: own-voice convergence (bot.py _consume_broll_ownvoice) —
voiceover_fn-шим `copyfile(mp3, out)` → немодифицированный assemble_broll_from_draft.
assemble НЕ расщепляется и НЕ меняется.

Запуск: python tests/test_broll_voiceover_gate.py
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
        self.sends = []        # send_message
        self.audios = []       # send_audio
        self.videos = []       # send_video

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return _FakeMsg()

    async def send_audio(self, chat_id, audio=None, reply_markup=None, **kw):
        self.audios.append({"chat_id": chat_id, "reply_markup": reply_markup})
        return _FakeMsg()

    async def send_video(self, chat_id, video=None, reply_markup=None, **kw):
        self.videos.append({"chat_id": chat_id, "reply_markup": reply_markup})
        return _FakeMsg()


def _ctx(broll_draft):
    return SimpleNamespace(bot=_FakeBot(), user_data={"broll_draft": broll_draft})


def _update(uid=7, chat=42):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(id=chat),
        callback_query=None,
        message=None,
    )


class _FakeVoiceover:
    """voiceover_fn-двойник: пишет dummy mp3 (>1000 байт) и считает вызовы =
    счётчик реальных обращений к ElevenLabs."""
    def __init__(self):
        self.calls = 0

    def __call__(self, script, out_path, *a, **k):
        self.calls += 1
        Path(out_path).write_bytes(b"\x00" * 2048)


def _draft(chat=42):
    return {
        "script": "Тестовый закадровый сценарий про картинг.",
        "clips": ["/tmp/nonexistent_clip.mp4"],  # assemble_montage застаблен — путь не читается
        "theme": "тест",
        "notion_url": None,
        "notion_page_id": None,  # None → status_fn не дёргается
        "chat_id": chat,
    }


# ── Тесты ────────────────────────────────────────────────────────────

def test_voiceover_gate_keyboard(errors):
    print("\n[_voiceover_gate_keyboard — accept/regen + реюз own/cancel]")
    cbs = _cbs(bh._voiceover_gate_keyboard())
    _assert("b2vop:accept" in cbs, f"кнопка «Собрать» (b2vop:accept): {cbs}", errors)
    _assert("b2vop:regen" in cbs, "кнопка «Перегенерировать» (b2vop:regen)", errors)
    _assert("b2vc:own" in cbs, "реюз существующего own-voice (b2vc:own)", errors)
    _assert("broll_cancel" in cbs, "реюз существующего broll_cancel", errors)


def test_ai_choice_previews_not_montage(errors):
    print("\n[preview_broll_voiceover — превью аудио + гейт, монтаж НЕ запущен]")
    fake = _FakeVoiceover()
    ctx = _ctx(_draft())
    asyncio.run(bh.preview_broll_voiceover(_update(), ctx, fake, chat_id=42, status_fn=None))
    _assert(fake.calls == 1, f"ElevenLabs дёрнут ровно 1 раз: {fake.calls}", errors)
    _assert(len(ctx.bot.audios) == 1, "аудио-превью отправлено (send_audio)", errors)
    _assert(len(ctx.bot.videos) == 0, "монтаж НЕ запущен (нет send_video)", errors)
    last_kb = ctx.bot.audios[-1]["reply_markup"] if ctx.bot.audios else None
    cbs = _cbs(last_kb)
    _assert("b2vop:accept" in cbs and "b2vop:regen" in cbs, f"гейт на превью: {cbs}", errors)
    _assert(ctx.user_data["broll_draft"].get("ai_voice_path"), "путь к mp3 сохранён в черновике", errors)


def test_accept_reuses_same_mp3_single_generation(errors):
    print("\n[accept_broll_voiceover — монтаж на ТОМ ЖЕ mp3, ElevenLabs 1×]")
    fake = _FakeVoiceover()
    captured = {}

    def _fake_montage(clip_paths, voiceover_path, output_path, tmp_dir=None, music_path=None, **kwargs):
        captured["voice_bytes"] = Path(voiceover_path).read_bytes()
        Path(output_path).write_bytes(b"\x00" * 100)  # dummy mp4

    orig_montage = bh.assemble_broll_montage
    bh.assemble_broll_montage = _fake_montage
    # Субтитры: пусть падают gracefully (assemble ловит и отдаёт ролик без них).
    try:
        ctx = _ctx(_draft())
        # 1. превью (генерация #1)
        asyncio.run(bh.preview_broll_voiceover(_update(), ctx, fake, chat_id=42, status_fn=None))
        # 2. accept — без voiceover_fn; шим копирует уже сгенерённый mp3
        asyncio.run(bh.accept_broll_voiceover(_update(), ctx, chat_id=42, status_fn=None))
    finally:
        bh.assemble_broll_montage = orig_montage

    _assert(fake.calls == 1, f"ElevenLabs суммарно 1 раз (шим, не повторная генерация): {fake.calls}", errors)
    _assert(captured.get("voice_bytes") == b"\x00" * 2048,
            "монтаж получил байты ИМЕННО превью-озвучки (reuse, не regen)", errors)
    _assert(len(ctx.bot.videos) == 1, "ролик собран и отправлен (send_video)", errors)


def test_regen_regenerates(errors):
    print("\n[regen_broll_voiceover — повторная генерация + новое превью]")
    fake = _FakeVoiceover()
    ctx = _ctx(_draft())
    asyncio.run(bh.preview_broll_voiceover(_update(), ctx, fake, chat_id=42, status_fn=None))
    asyncio.run(bh.regen_broll_voiceover(_update(), ctx, fake, chat_id=42, status_fn=None))
    _assert(fake.calls == 2, f"regen = +1 генерация: {fake.calls}", errors)
    _assert(len(ctx.bot.audios) == 2, "второе аудио-превью отправлено", errors)
    _assert(len(ctx.bot.videos) == 0, "regen не запускает монтаж", errors)


def test_bot_wiring_present(errors):
    print("\n[bot.py — b2vc:ai → превью, ветки b2vop зарегистрированы]")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    _assert("preview_broll_voiceover" in src, "b2vc:ai зовёт preview_broll_voiceover", errors)
    _assert('"b2vop:accept"' in src or "'b2vop:accept'" in src, "ветка b2vop:accept в handle_callback", errors)
    _assert('"b2vop:regen"' in src or "'b2vop:regen'" in src, "ветка b2vop:regen в handle_callback", errors)


def main():
    errors = []
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_drafts_test_"))
    test_voiceover_gate_keyboard(errors)
    test_ai_choice_previews_not_montage(errors)
    test_accept_reuses_same_mp3_single_generation(errors)
    test_regen_regenerates(errors)
    test_bot_wiring_present(errors)
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
