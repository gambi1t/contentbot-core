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


class _FakeStatusMsg:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class _FakeBot:
    def __init__(self):
        self.audios = []
        self.sends = []
        self.videos = []

    async def send_audio(self, chat_id, audio=None, reply_markup=None, **kw):
        self.audios.append({"chat_id": chat_id, "reply_markup": reply_markup, "kw": kw})

    async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
        self.sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return _FakeStatusMsg()

    async def send_video(self, chat_id, video=None, **kw):
        self.videos.append({"chat_id": chat_id})
        return _FakeStatusMsg()


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


def test_no_audio_sent_shows_error_not_footer():
    # все файлы не открылись (папка отмонтирована) → ошибка, НЕ «послушай выше»
    if not _DUMMY_MP3.exists():
        _DUMMY_MP3.write_bytes(b"\x00" * 4096)
    missing = "/nonexistent/dir/ghost.mp3"
    bot._card_pick_n_tracks = lambda cat, n=3, exclude_ids=None: [
        {"id": "g1", "file": missing, "duration": 80}]
    import music_mixer
    music_mixer.list_categories = lambda: {"chill": {"label": "Chill"}}
    bot._save_pending = lambda *a, **k: None
    ctx = _ctx()
    asyncio.run(bot._send_card_music_previews(ctx, 42, "chill", "CARD20", {}))
    assert len(ctx.bot.audios) == 0
    texts = " ".join(s["text"] or "" for s in ctx.bot.sends)
    assert "Не удалось загрузить" in texts, f"должно быть сообщение об ошибке: {texts}"
    assert "Послушай" not in texts, "не должно быть футера «послушай» без аудио"


# ── HIGH-фикс: music_apply: НЕ редактирует аудио-сообщение ────────────

def test_apply_card_music_no_edit_on_audio(monkeypatch, tmp_path):
    """Кнопка music_apply: висит на АУДИО-сообщении (нет текста) → хелпер не
    должен звать query.edit_message_text (упало бы 400); мукс должен выполниться."""
    if not _DUMMY_MP3.exists():
        _DUMMY_MP3.write_bytes(b"\x00" * 4096)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "final_video.mp4").write_bytes(b"\x00" * 200)
    monkeypatch.setattr(bot, "_project_dir", lambda data: proj)
    monkeypatch.setattr(music_mixer, "list_categories", lambda: {"chill": {"label": "Chill"}})
    monkeypatch.setattr(music_mixer, "list_tracks",
                        lambda cat: [{"id": "chill_1", "file": str(_DUMMY_MP3), "duration": 90}])
    mixed = {}

    def _mix(src, track, out):
        mixed["called"] = True
        Path(out).write_bytes(b"\x00" * 300)
        return True

    monkeypatch.setattr(music_mixer, "mix_music_into_video", _mix)

    edited = {"audio_edit": 0}

    class _AudioQuery:
        def __init__(self):
            self.message = SimpleNamespace(chat_id=42)  # АУДИО: нет .text

        async def edit_message_text(self, *a, **k):
            edited["audio_edit"] += 1  # НЕ должно вызываться

        async def answer(self, *a, **k):
            pass

    ctx = _ctx()
    asyncio.run(bot._apply_card_music(ctx, _AudioQuery(), {"x": 1}, "chill_1", "CARD20"))
    assert mixed.get("called"), "мукс выбранного трека должен выполниться (happy path)"
    assert edited["audio_edit"] == 0, "НЕ редактируем аудио-сообщение (query.edit_message_text)"
    assert len(ctx.bot.videos) == 1, "смикшированный ролик отправлен"


def test_apply_card_music_double_click_single_mix(monkeypatch, tmp_path):
    """H1: двойной клик/повторный callback → РОВНО один мукс (lock), второй —
    «уже микширую»."""
    if not _DUMMY_MP3.exists():
        _DUMMY_MP3.write_bytes(b"\x00" * 4096)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "final_video.mp4").write_bytes(b"\x00" * 200)
    monkeypatch.setattr(bot, "_project_dir", lambda data: proj)
    monkeypatch.setattr(music_mixer, "list_categories", lambda: {"chill": {"label": "Chill"}})
    monkeypatch.setattr(music_mixer, "list_tracks",
                        lambda cat: [{"id": "chill_1", "file": str(_DUMMY_MP3), "duration": 90}])
    mix = {"n": 0}

    def _slow_mix(src, track, out):
        mix["n"] += 1
        Path(out).write_bytes(b"\x00" * 300)
        return True

    monkeypatch.setattr(music_mixer, "mix_music_into_video", _slow_mix)
    answers = []

    class _Q:
        def __init__(self):
            self.message = SimpleNamespace(chat_id=42)
            self.from_user = SimpleNamespace(id=7)

        async def answer(self, *a, **k):
            answers.append(a[0] if a else "")

    ctx = _ctx()

    async def _both():
        import asyncio as _aio
        await _aio.gather(
            bot._apply_card_music(ctx, _Q(), {"x": 1}, "chill_1", "CARD20"),
            bot._apply_card_music(ctx, _Q(), {"x": 1}, "chill_1", "CARD20"),
        )

    asyncio.run(_both())
    assert mix["n"] == 1, f"при double-click должен быть РОВНО один мукс: {mix['n']}"
    assert any("же микши" in (a or "") for a in answers), f"второй клик → «уже микширую»: {answers}"


def test_callback_64_byte_guard():
    # H4: для разумных id callback ≤ 64 байт; гард на переполнение есть в источнике.
    card_prefix = "X" * 20
    assert len(f"music_apply:chill_corporate_01:{card_prefix}".encode("utf-8")) <= 64
    assert "len(_cb.encode" in SRC and "> 64" in SRC, "нет 64-байт гарда в пикере"


def test_overlong_callback_track_skipped():
    # H4: трек с переполняющим callback (длинный id) пропускается, не падает на send.
    if not _DUMMY_MP3.exists():
        _DUMMY_MP3.write_bytes(b"\x00" * 4096)
    import music_mixer as _mm
    _mm.list_categories = lambda: {"chill": {"label": "Chill"}}
    bot._save_pending = lambda *a, **k: None
    bot._card_pick_n_tracks = lambda cat, n=3, exclude_ids=None: [
        {"id": "x" * 80, "file": str(_DUMMY_MP3), "duration": 90}]
    ctx = _ctx()
    asyncio.run(bot._send_card_music_previews(ctx, 42, "chill", "X" * 20, {}))
    assert len(ctx.bot.audios) == 0, "трек с >64-байт callback не должен отправляться"


def test_music_apply_handler_delegates_to_helper():
    assert "_apply_card_music" in SRC, "music_apply: не вынесен в хелпер"
    # в новом music_apply: нет прямого query.edit_message_text (он в старом коде ломал аудио)
    apply_block = SRC.split('if query.data.startswith("music_apply:")', 1)[-1][:400]
    assert "query.edit_message_text" not in apply_block, "music_apply: всё ещё редактирует сообщение-кнопку"


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
