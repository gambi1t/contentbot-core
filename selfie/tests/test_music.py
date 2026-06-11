"""Тесты selfie.music — pure helpers + клавиатуры выбора музыки.

Реальный ffmpeg-микс тестируется через Telethon (сценарии 18/19), здесь
тестируем только helpers без I/O.

Запуск: python selfie/tests/test_music.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from selfie import music  # noqa: E402


# ── Тестовые fixtures категорий ─────────────────────────────────────────────

FAKE_CATEGORIES = {
    "chill": {"label": "Спокойный / Фон", "emoji": "😌", "desc": "Для обучения"},
    "energetic": {"label": "Драйв / Энергия", "emoji": "🔥", "desc": "Для AI"},
    "corporate": {"label": "Деловой", "emoji": "💼", "desc": "Для B2B"},
    "cinematic": {"label": "Кинематограф", "emoji": "🎬", "desc": "Для эпика"},
    "inspiring": {"label": "Вдохновляющий", "emoji": "✨", "desc": "Для мотивации"},
}

FAKE_TRACKS = {
    "chill": [
        {"id": "chill_102", "file": "/root/music/chill/chill_102.mp3", "duration": 99.6, "size_mb": 3.0},
        {"id": "chill_1076", "file": "/root/music/chill/chill_1076.mp3", "duration": 96.6, "size_mb": 2.9},
        {"id": "chill_1087", "file": "/root/music/chill/chill_1087.mp3", "duration": 99.6, "size_mb": 3.0},
    ],
    "energetic": [
        {"id": "energetic_1003", "file": "/root/music/energetic/energetic_1003.mp3", "duration": 151.0, "size_mb": 4.6},
    ],
}


# ── get_visible_categories ──────────────────────────────────────────────────

def test_get_visible_categories_returns_all_five_in_order():
    """Категории возвращаются в фиксированном порядке (chill, energetic, ...)."""
    with patch("selfie.music._list_cats", return_value=FAKE_CATEGORIES):
        cats = music.get_visible_categories()
    assert len(cats) == 5, f"Expected 5 cats, got {len(cats)}"
    keys = [c["cat"] for c in cats]
    assert keys == ["chill", "energetic", "corporate", "cinematic", "inspiring"], \
        f"Wrong order: {keys}"
    print("  OK 5 categories in fixed order")


def test_get_visible_categories_skips_missing():
    """Если категории нет в tracks.json — она не появляется в выдаче."""
    partial = {"chill": FAKE_CATEGORIES["chill"], "energetic": FAKE_CATEGORIES["energetic"]}
    with patch("selfie.music._list_cats", return_value=partial):
        cats = music.get_visible_categories()
    keys = [c["cat"] for c in cats]
    assert keys == ["chill", "energetic"], f"Should only return present cats, got: {keys}"
    print("  OK skips missing categories")


def test_get_visible_categories_includes_label_and_emoji():
    """Каждая категория содержит label и emoji для UI."""
    with patch("selfie.music._list_cats", return_value=FAKE_CATEGORIES):
        cats = music.get_visible_categories()
    for c in cats:
        assert "cat" in c and "label" in c and "emoji" in c, f"Missing fields: {c}"
        assert c["label"], f"Empty label in {c}"
    print("  OK each cat has label + emoji")


# ── pick_random_track ───────────────────────────────────────────────────────

def test_pick_random_track_returns_track_from_category():
    """Возвращает один из треков указанной категории."""
    with patch("selfie.music._list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        for _ in range(20):
            t = music.pick_random_track("chill")
            assert t is not None
            assert t["id"].startswith("chill_"), f"Track from wrong cat: {t}"
    print("  OK random track from category")


def test_pick_random_track_returns_none_for_empty_category():
    """Пустая категория → None (не падать)."""
    with patch("selfie.music._list_tracks", return_value=[]):
        assert music.pick_random_track("nonexistent") is None
    print("  OK empty category returns None")


def test_pick_random_track_excludes_given_id():
    """exclude_id → не возвращаем тот же трек (если есть альтернативы)."""
    with patch("selfie.music._list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        for _ in range(30):
            t = music.pick_random_track("chill", exclude_id="chill_102")
            assert t is not None
            assert t["id"] != "chill_102", f"Should have excluded chill_102, got: {t['id']}"
    print("  OK exclude_id avoided")


def test_pick_random_track_fallback_when_all_excluded():
    """Если только 1 трек И он excluded — вернуть его всё равно (лучше что-то чем ничего)."""
    single = [{"id": "only_one", "file": "/x.mp3", "duration": 90}]
    with patch("selfie.music._list_tracks", return_value=single):
        t = music.pick_random_track("any", exclude_id="only_one")
    assert t is not None
    assert t["id"] == "only_one"
    print("  OK fallback when all excluded")


# ── pick_n_tracks (превью: 3 трека на прослушивание) ────────────────────────

def test_pick_n_tracks_returns_n_distinct():
    """Возвращает n РАЗНЫХ треков из категории."""
    with patch("selfie.music._list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        tracks = music.pick_n_tracks("chill", n=3)
    assert len(tracks) == 3, f"Expected 3, got {len(tracks)}"
    ids = [t["id"] for t in tracks]
    assert len(set(ids)) == 3, f"Дубликаты в превью: {ids}"
    print("  OK pick_n_tracks 3 distinct")


def test_pick_n_tracks_caps_at_available():
    """n больше доступного → возвращает сколько есть (energetic = 1 трек)."""
    with patch("selfie.music._list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        tracks = music.pick_n_tracks("energetic", n=3)
    assert len(tracks) == 1, f"Expected 1 (всего 1 трек), got {len(tracks)}"
    print("  OK pick_n_tracks caps at available")


def test_pick_n_tracks_empty_category():
    """Пустая категория → пустой список (не падение)."""
    with patch("selfie.music._list_tracks", return_value=[]):
        assert music.pick_n_tracks("nope", n=3) == []
    print("  OK pick_n_tracks empty → []")


def test_pick_n_tracks_excludes_shown():
    """exclude_ids (уже показанные для reroll) исключаются из выдачи."""
    with patch("selfie.music._list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        tracks = music.pick_n_tracks("chill", n=2, exclude_ids=["chill_102"])
    ids = [t["id"] for t in tracks]
    assert "chill_102" not in ids, f"Показанный трек не исключён: {ids}"
    assert len(tracks) == 2, f"Expected 2 из оставшихся, got {len(tracks)}"
    print("  OK pick_n_tracks excludes shown")


def test_pick_n_tracks_fallback_when_all_excluded():
    """Все треки в exclude → fallback (дать любые n, чем пусто после reroll)."""
    with patch("selfie.music._list_tracks", side_effect=lambda c: FAKE_TRACKS.get(c, [])):
        tracks = music.pick_n_tracks("chill", n=2, exclude_ids=["chill_102", "chill_1076", "chill_1087"])
    assert len(tracks) == 2, f"fallback должен дать 2, got {len(tracks)}"
    print("  OK pick_n_tracks fallback when all excluded")


# ── category_keyboard ───────────────────────────────────────────────────────

def test_category_keyboard_has_all_categories_plus_skip():
    """Клавиатура содержит все 5 категорий + «Без музыки» + «Отмена»."""
    with patch("selfie.music._list_cats", return_value=FAKE_CATEGORIES):
        kb = music.category_keyboard()
    # Собираем все callback_data
    all_data = []
    for row in kb.inline_keyboard:
        for btn in row:
            all_data.append(btn.callback_data)
    # 5 категорий
    cat_callbacks = [d for d in all_data if d.startswith("selfie_music:cat:")]
    assert len(cat_callbacks) == 5, f"Expected 5 cat buttons, got {len(cat_callbacks)}: {cat_callbacks}"
    # skip
    assert "selfie_music:skip" in all_data, "Missing skip button"
    # cancel
    assert "cancel" in all_data, "Missing cancel button"
    print("  OK keyboard has 5 cats + skip + cancel")


def test_category_keyboard_buttons_have_labels():
    """Кнопки категорий — с человеческими подписями (label + emoji)."""
    with patch("selfie.music._list_cats", return_value=FAKE_CATEGORIES):
        kb = music.category_keyboard()
    cat_btns = [b for row in kb.inline_keyboard for b in row
                if b.callback_data.startswith("selfie_music:cat:")]
    for b in cat_btns:
        assert b.text.strip(), f"Empty button text: {b.callback_data}"
        # Должна быть хотя бы пара символов помимо emoji
        assert len(b.text) > 3, f"Too short label: {b.text!r}"
    print("  OK buttons have meaningful labels")


# ── picked_keyboard ─────────────────────────────────────────────────────────

def test_picked_keyboard_has_accept_reroll_back_skip():
    """После выбора трека — 4 действия: accept, reroll, back, skip."""
    kb = music.picked_keyboard("chill")
    all_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "selfie_music:accept" in all_data, "Missing accept"
    assert "selfie_music:reroll:chill" in all_data, "Missing reroll with cat"
    assert "selfie_music:back" in all_data, "Missing back"
    assert "selfie_music:skip" in all_data, "Missing skip"
    print("  OK picked keyboard has accept/reroll/back/skip")


def test_picked_keyboard_reroll_preserves_category():
    """reroll callback включает текущую категорию (чтобы знать что rerollить)."""
    kb = music.picked_keyboard("energetic")
    all_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "selfie_music:reroll:energetic" in all_data
    print("  OK reroll preserves category in callback")


# ── build messages ──────────────────────────────────────────────────────────

def test_picker_message_mentions_music_choice():
    msg = music.build_music_picker_message()
    low = msg.lower()
    assert "музык" in low or "music" in low, f"No music mention: {msg!r}"
    print("  OK picker message mentions music")


def test_picked_message_includes_track_info():
    """Сообщение после mix — содержит id трека и длительность."""
    track = {"id": "chill_102", "file": "/x.mp3", "duration": 99.6, "size_mb": 3.0}
    msg = music.build_picked_message("Спокойный / Фон", track)
    assert "chill_102" in msg, f"No track id: {msg}"
    assert "Спокойный" in msg, f"No category label: {msg}"
    print("  OK picked message has track id and category")


def test_picked_message_handles_missing_video_size():
    """video_size_mb опциональный — без него не падать."""
    track = {"id": "energetic_1", "file": "/x.mp3", "duration": 120.0}
    msg = music.build_picked_message("Драйв", track, video_size_mb=None)
    assert msg  # просто не упало
    print("  OK picked message works without video_size_mb")


# ── runner ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import inspect
    tests = [(n, fn) for n, fn in globals().items() if n.startswith("test_") and callable(fn)]
    failed = 0
    print(f"\n{'='*70}\nRunning {len(tests)} tests in selfie/tests/test_music.py\n{'='*70}")
    for name, fn in tests:
        print(f"> {name}")
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"  X FAIL: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{'='*70}\n{'GREEN' if failed == 0 else 'RED'}: {len(tests)-failed}/{len(tests)} passed\n{'='*70}")
    sys.exit(0 if failed == 0 else 1)
