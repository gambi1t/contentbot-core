"""TDD: B-roll Pipeline 2 — выбор фоновой музыки (инкремент 3).

Артём (17 июня): довести Pipeline 2 до стандартного пайплайна. Гейт #3:
на «Собрать ролик» (ДО развилки голоса) показать выбор музыки по категориям;
выбранный трек подмешивается под озвучку в монтаже. Размещение ДО голоса =
одна точка вставки, покрывает оба голосовых форка (ИИ и свой), т.к. оба
сходятся на assemble_broll_from_draft, читающем broll_draft['music_path'].

Реюз: music_mixer.list_categories/pick_random_track (shared, в проде) +
assemble_broll_montage(music_path=...) — уже микширует (volume 0.18, loop),
ассемблер НЕ меняется. UI селфи не годится verbatim (state-gated + премикс),
поэтому тонкие b2mus:-клавиатуры + capture-only хендлер.

Запуск: python tests/test_broll_music_pick.py
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
        self.deleted = False
        self.edits = []

    async def delete(self):
        self.deleted = True

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class _FakeBot:
    def __init__(self):
        self.sends = []
        self.audios = []
        self.videos = []

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


_DUMMY_MP3 = None


def _install_music_fakes():
    """Фейки музыкальной библиотеки: реальный dummy-mp3 (хендлер открывает файл
    для send_audio), без зависимости от диска сервера."""
    global _DUMMY_MP3
    if _DUMMY_MP3 is None:
        _DUMMY_MP3 = bh.DRAFTS_DIR / "dummy_track.mp3"
        _DUMMY_MP3.write_bytes(b"\x00" * 4096)
    bh.list_categories = lambda: {"chill": {}, "energetic": {}, "cinematic": {}}
    bh.pick_random_track = lambda category, exclude_id=None: {
        "id": f"{category}_1", "file": str(_DUMMY_MP3), "duration": 90, "size_mb": 1.2,
    }


# ── Тесты ────────────────────────────────────────────────────────────

def test_category_keyboard(errors):
    print("\n[_music_category_keyboard — категории + без музыки + реюз cancel]")
    _install_music_fakes()
    cbs = _cbs(bh._music_category_keyboard())
    _assert("b2mus:cat:chill" in cbs, f"кнопка категории chill: {cbs}", errors)
    _assert("b2mus:cat:energetic" in cbs, "кнопка категории energetic", errors)
    _assert("b2mus:skip" in cbs, "кнопка «без музыки» (b2mus:skip)", errors)
    _assert("broll_cancel" in cbs, "реюз существующего broll_cancel", errors)


def test_picked_keyboard(errors):
    print("\n[_music_picked_keyboard — принять/другой/категория/без музыки]")
    cbs = _cbs(bh._music_picked_keyboard("chill"))
    _assert("b2mus:accept" in cbs, f"принять (b2mus:accept): {cbs}", errors)
    _assert("b2mus:reroll:chill" in cbs, "другой трек (b2mus:reroll:<cat>)", errors)
    _assert("b2mus:back" in cbs, "сменить категорию (b2mus:back)", errors)
    _assert("b2mus:skip" in cbs, "без музыки (b2mus:skip)", errors)


def test_cat_previews_and_stores(errors):
    print("\n[b2mus:cat — превью аудио трека + music_path в черновик]")
    _install_music_fakes()
    ctx = _ctx(_draft())
    asyncio.run(bh.handle_broll_music_cb(_update(), ctx, "cat", category="chill", chat_id=42))
    _assert(len(ctx.bot.audios) == 1, "аудио-превью трека отправлено", errors)
    _assert(ctx.user_data["broll_draft"].get("music_path") == str(_DUMMY_MP3),
            f"путь трека сохранён: {ctx.user_data['broll_draft'].get('music_path')}", errors)
    cbs = _cbs(ctx.bot.audios[-1]["reply_markup"])
    _assert("b2mus:accept" in cbs and "b2mus:reroll:chill" in cbs, f"клавиатура выбранного: {cbs}", errors)


def test_skip_clears_and_continues_to_voice(errors):
    print("\n[b2mus:skip — music_path снят, переход к развилке голоса]")
    _install_music_fakes()
    ctx = _ctx(_draft(music_path="/srv/music/chill_1.mp3"))
    asyncio.run(bh.handle_broll_music_cb(_update(), ctx, "skip", chat_id=42))
    _assert(not ctx.user_data["broll_draft"].get("music_path"), "music_path снят (без музыки)", errors)
    all_cbs = [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert("b2vc:ai" in all_cbs, "показана развилка голоса (b2vc:ai)", errors)


def test_accept_keeps_music_and_continues(errors):
    print("\n[b2mus:accept — music_path сохранён, переход к голосу]")
    _install_music_fakes()
    ctx = _ctx(_draft(music_path="/srv/music/chill_1.mp3"))
    asyncio.run(bh.handle_broll_music_cb(_update(), ctx, "accept", chat_id=42))
    _assert(ctx.user_data["broll_draft"].get("music_path") == "/srv/music/chill_1.mp3",
            "music_path сохранён после accept", errors)
    all_cbs = [c for s in ctx.bot.sends for c in _cbs(s.get("reply_markup"))]
    _assert("b2vc:ai" in all_cbs, "показана развилка голоса", errors)


def test_music_threaded_into_assemble(errors):
    print("\n[assemble_broll_from_draft — music_path прокинут в монтаж]")
    captured = {}

    def _fake_montage(clip_paths, voiceover_path, output_path, tmp_dir=None, music_path=None):
        captured["music_path"] = music_path
        Path(output_path).write_bytes(b"\x00" * 100)

    def _fake_voiceover(text, out_path, *a, **k):
        Path(out_path).write_bytes(b"\x00" * 2048)

    orig = bh.assemble_broll_montage
    bh.assemble_broll_montage = _fake_montage
    try:
        # с музыкой
        ctx = _ctx(_draft(music_path="/srv/music/chill_1.mp3"))
        asyncio.run(bh.assemble_broll_from_draft(_update(), ctx, _fake_voiceover, chat_id=42, status_fn=None))
        _assert(captured.get("music_path") == "/srv/music/chill_1.mp3",
                f"монтаж получил music_path: {captured.get('music_path')}", errors)
        # без музыки → None (текущее поведение сохранено)
        captured.clear()
        ctx2 = _ctx(_draft())
        asyncio.run(bh.assemble_broll_from_draft(_update(), ctx2, _fake_voiceover, chat_id=42, status_fn=None))
        _assert(captured.get("music_path") is None, "без выбора — music_path=None (voice-only)", errors)
    finally:
        bh.assemble_broll_montage = orig


def test_bot_wiring_present(errors):
    print("\n[bot.py — broll_approve → музыка, ветка b2mus]")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    _assert("start_broll_music_pick" in src, "broll_approve зовёт start_broll_music_pick", errors)
    _assert('"b2mus:"' in src or "'b2mus:'" in src, "ветка b2mus в handle_callback", errors)


def main():
    errors = []
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_drafts_test_"))
    test_category_keyboard(errors)
    test_picked_keyboard(errors)
    test_cat_previews_and_stores(errors)
    test_skip_clears_and_continues_to_voice(errors)
    test_accept_keeps_music_and_continues(errors)
    test_music_threaded_into_assemble(errors)
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
