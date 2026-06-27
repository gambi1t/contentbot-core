"""TDD: Fix #2 — карточка/аватар: 3-трековый АУДИО-пикер музыки вместо текста.

Баг (запись SMM 25.06): в пайплайне карточки/аватара `music_cat:` показывал
ТЕКСТ-кнопки «🎵 Трек N (45с)» БЕЗ аудио — нельзя прослушать. Фикс (Q3 reuse):
переиспользуем `selfie.music.pick_n_tracks` + паттерн `_send_track_previews`
(3× send_audio + кнопка «✅ Выбрать этот» = `music_apply:<id>:<prefix>` под
каждым), вынесено в хелпер `_send_card_music_previews`. `music_apply:` /
`music_pick:` НЕ меняются (формат callback тот же → мукс остаётся как был).

Запуск: python -m pytest tests/test_card_music_audio_picker.py -v
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import music_mixer  # noqa: E402

SRC = (ROOT / "bot.py").read_text(encoding="utf-8")
_DUMMY_MP3 = ROOT / "tests" / "_dummy_card_track.mp3"


def _cbs(markup):
    if markup is None:
        return []
    return [getattr(b, "callback_data", None) for row in markup.inline_keyboard for b in row]


class _FakeBot:
    def __init__(self):
        self.audios = []
        self.sends = []

    async def send_audio(self, chat_id, audio=None, reply_markup=None, **kw):
        self.audios.append({"chat_id": chat_id, "reply_markup": reply_markup, "kw": kw})

    async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
        self.sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


def _ctx():
    return SimpleNamespace(bot=_FakeBot())


def _install_fakes(n_tracks=5):
    """Фейк музыкальной библиотеки: реальный dummy-mp3 (хендлер open()'ит файл
    для send_audio), без зависимости от диска сервера."""
    if not _DUMMY_MP3.exists():
        _DUMMY_MP3.write_bytes(b"\x00" * 4096)
    tracks = [
        {"id": f"chill_{i}", "file": str(_DUMMY_MP3), "duration": 90 - i, "size_mb": 1.1}
        for i in range(1, n_tracks + 1)
    ]

    def _pick(cat, n=3, exclude_ids=None):
        pool = [t for t in tracks if t["id"] not in (exclude_ids or [])]
        if not pool:
            pool = list(tracks)
        return pool[:n]

    bot._card_pick_n_tracks = _pick
    music_mixer.list_categories = lambda: {
        "chill": {"emoji": "🌊", "label": "Chill", "desc": "спокойная"}
    }
    bot._save_pending = lambda *a, **k: None
    return tracks


# ── Поведенческие тесты хелпера ──────────────────────────────────────

def test_sends_3_audio_previews():
    _install_fakes()
    ctx = _ctx()
    data = {}
    asyncio.run(bot._send_card_music_previews(ctx, 42, "chill", "CARD20", data))
    assert len(ctx.bot.audios) == 3, f"должно быть 3 аудио-превью, а не {len(ctx.bot.audios)}"


def test_pick_button_under_each_audio():
    _install_fakes()
    ctx = _ctx()
    asyncio.run(bot._send_card_music_previews(ctx, 42, "chill", "CARD20", {}))
    pick = [c for a in ctx.bot.audios for c in _cbs(a["reply_markup"])]
    assert pick and all(c and c.startswith("music_apply:") and c.endswith(":CARD20") for c in pick), (
        f"под каждым аудио кнопка music_apply:<id>:<prefix>: {pick}"
    )


def test_footer_reroll_back_card():
    _install_fakes()
    ctx = _ctx()
    asyncio.run(bot._send_card_music_previews(ctx, 42, "chill", "CARD20", {}))
    footer = [c for s in ctx.bot.sends for c in _cbs(s["reply_markup"])]
    assert "music_cat:chill:CARD20" in footer, f"reroll (другие треки): {footer}"
    assert "music_pick:CARD20" in footer, f"назад к категориям: {footer}"
    assert "notion_card:CARD20" in footer, f"к карточке: {footer}"


def test_shown_ids_stored():
    _install_fakes()
    ctx = _ctx()
    data = {}
    asyncio.run(bot._send_card_music_previews(ctx, 42, "chill", "CARD20", data))
    assert len(data.get("music_shown_ids") or []) == 3, (
        f"3 показанных id запомнены: {data.get('music_shown_ids')}"
    )


def test_reroll_excludes_shown():
    _install_fakes()
    captured = {}
    base = bot._card_pick_n_tracks

    def _spy(cat, n=3, exclude_ids=None):
        captured["exclude_ids"] = list(exclude_ids or [])
        return base(cat, n=n, exclude_ids=exclude_ids)

    bot._card_pick_n_tracks = _spy
    ctx = _ctx()
    data = {}
    asyncio.run(bot._send_card_music_previews(ctx, 42, "chill", "CARD20", data))
    first = list(data.get("music_shown_ids") or [])
    asyncio.run(bot._send_card_music_previews(ctx, 42, "chill", "CARD20", data))
    assert first and set(first).issubset(set(captured.get("exclude_ids", []))), (
        f"reroll исключил показанные {first}: exclude={captured.get('exclude_ids')}"
    )


# ── Source-level: проводка в обработчиках ────────────────────────────

def test_music_cat_uses_audio_helper_not_text():
    assert "_send_card_music_previews" in SRC, "хелпер аудио-пикера не подключён"
    # старая текст-кнопка «🎵 Трек N» должна исчезнуть из music_cat:
    assert 'Трек {i}' not in SRC and 'Трек {i} (' not in SRC, "остался старый текст-пикер «🎵 Трек N»"


def test_reuses_selfie_pick_n_tracks():
    assert "_card_pick_n_tracks" in SRC, "нет reuse selfie.music.pick_n_tracks"
    assert "from selfie.music import pick_n_tracks" in SRC, "pick_n_tracks не импортирован из selfie.music"


def test_avatar_publish_has_music_button():
    # avatar_publish (субтитры на аватар → final_video.mp4) должен давать вход в
    # музыку. До фикса было 2 входа (auto-assemble final + card menu); добавляем
    # 3-й на финале avatar_publish, где есть final_video.mp4.
    assert SRC.count('music_pick:') >= 3, (
        "на финале avatar_publish не добавлена кнопка музыки (вход в аудио-пикер)"
    )


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
