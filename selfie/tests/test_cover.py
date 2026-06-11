"""Тесты selfie.cover — pure helpers для выбора обложки.

Тестируем helpers + keyboards. Реальный ffmpeg-snapshot тестируется через
Telethon (сценарии 20+). Реальная фото-библиотека на сервере — патчим через
мок _LIBRARY_DIR.

Запуск: python selfie/tests/test_cover.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from selfie import cover  # noqa: E402


# ── get_frame_timestamps ────────────────────────────────────────────────────

def test_get_frame_timestamps_short_video():
    """Видео 10 сек → [1.0, 5.0, 9.0]."""
    ts = cover.get_frame_timestamps(10.0)
    assert len(ts) == 3, f"Expected 3 timestamps, got {len(ts)}"
    assert ts[0] == 1.0, f"first should be 1.0, got {ts[0]}"
    assert 4.0 <= ts[1] <= 6.0, f"middle should be ~5.0, got {ts[1]}"
    assert 8.0 <= ts[2] < 10.0, f"end should be ~9.0, got {ts[2]}"
    print("  OK 10s video → 3 timestamps spread evenly")


def test_get_frame_timestamps_long_video():
    """Видео 60 сек → [1.0, 30.0, 59.0]."""
    ts = cover.get_frame_timestamps(60.0)
    assert ts[0] == 1.0
    assert 29.0 <= ts[1] <= 31.0
    assert 58.0 <= ts[2] < 60.0
    print("  OK 60s video → spread")


def test_get_frame_timestamps_very_short_video():
    """Видео 2 сек — не падать, все три кадра ≤ длительности."""
    ts = cover.get_frame_timestamps(2.0)
    assert len(ts) == 3
    for t in ts:
        assert 0 <= t < 2.0, f"Timestamp {t} out of range for 2s video"
    print("  OK very short video clamped")


def test_get_frame_timestamps_returns_floats():
    ts = cover.get_frame_timestamps(30.0)
    for t in ts:
        assert isinstance(t, float)
    print("  OK returns floats")


# ── list_library_sample ─────────────────────────────────────────────────────

def _make_fake_library(tmp_root: Path, n_files: int = 15) -> Path:
    """Создать tmp фейк-библиотеку с n .jpg файлами."""
    lib = tmp_root / "library"
    sub = lib / "category"
    sub.mkdir(parents=True)
    for i in range(n_files):
        (sub / f"photo_{i:03d}.jpg").write_bytes(b"fake")
    return lib


def test_list_library_sample_returns_correct_count():
    """list_library_sample(6) → ровно 6 случайных файлов."""
    with tempfile.TemporaryDirectory() as tmp:
        lib = _make_fake_library(Path(tmp), n_files=20)
        with patch("selfie.cover._LIBRARY_DIR", lib):
            sample = cover.list_library_sample(n=6)
        assert len(sample) == 6, f"Expected 6, got {len(sample)}"
        for item in sample:
            assert "path" in item and "id" in item, f"Missing keys: {item}"
            assert Path(item["path"]).exists(), f"Path doesn't exist: {item['path']}"
    print("  OK returns 6 items with path+id")


def test_list_library_sample_when_fewer_than_n_files():
    """Библиотека < n → возвращаем все что есть."""
    with tempfile.TemporaryDirectory() as tmp:
        lib = _make_fake_library(Path(tmp), n_files=3)
        with patch("selfie.cover._LIBRARY_DIR", lib):
            sample = cover.list_library_sample(n=6)
        assert len(sample) == 3, f"Expected 3 (all), got {len(sample)}"
    print("  OK fewer files → all returned")


def test_list_library_sample_excludes_given_ids():
    """exclude_ids: не возвращаем те же id (для reroll)."""
    with tempfile.TemporaryDirectory() as tmp:
        lib = _make_fake_library(Path(tmp), n_files=20)
        with patch("selfie.cover._LIBRARY_DIR", lib):
            first = cover.list_library_sample(n=6)
            exclude = [item["id"] for item in first]
            second = cover.list_library_sample(n=6, exclude_ids=exclude)
        # second не должен пересекаться с exclude
        first_ids = {item["id"] for item in first}
        second_ids = {item["id"] for item in second}
        overlap = first_ids & second_ids
        assert not overlap, f"Reroll returned excluded ids: {overlap}"
    print("  OK exclude_ids honored for reroll")


def test_list_library_sample_empty_dir():
    """Пустая директория → пустой список (не падать)."""
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty"
        empty.mkdir()
        with patch("selfie.cover._LIBRARY_DIR", empty):
            sample = cover.list_library_sample(n=6)
        assert sample == [], f"Expected empty, got {sample}"
    print("  OK empty dir → empty list")


def test_list_library_sample_missing_dir():
    """Несуществующая директория → пустой список."""
    with patch("selfie.cover._LIBRARY_DIR", Path("/nonexistent/path/x/y/z")):
        sample = cover.list_library_sample(n=6)
    assert sample == []
    print("  OK missing dir → empty list")


def test_lookup_library_path_returns_existing_path():
    """lookup_library_path(id) → возвращает путь если id есть в библиотеке."""
    with tempfile.TemporaryDirectory() as tmp:
        lib = _make_fake_library(Path(tmp), n_files=5)
        with patch("selfie.cover._LIBRARY_DIR", lib):
            sample = cover.list_library_sample(n=3)
            target_id = sample[0]["id"]
            target_path = sample[0]["path"]
            resolved = cover.lookup_library_path(target_id)
        assert resolved == target_path, f"Expected {target_path}, got {resolved}"
    print("  OK lookup_library_path resolves id → path")


def test_lookup_library_path_missing_id():
    """Несуществующий id → None."""
    with tempfile.TemporaryDirectory() as tmp:
        lib = _make_fake_library(Path(tmp), n_files=3)
        with patch("selfie.cover._LIBRARY_DIR", lib):
            assert cover.lookup_library_path("nonexistent_id_xyz") is None
    print("  OK missing id → None")


# ── Keyboards ───────────────────────────────────────────────────────────────

def test_cover_picker_keyboard_has_all_options():
    """Picker должен содержать: 3 кадра, upload, library, skip, cancel."""
    kb = cover.cover_picker_keyboard()
    all_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "selfie_cover:frame:start" in all_data
    assert "selfie_cover:frame:mid" in all_data
    assert "selfie_cover:frame:end" in all_data
    assert "selfie_cover:upload" in all_data
    assert "selfie_cover:library" in all_data
    assert "selfie_cover:skip" in all_data
    assert "cancel" in all_data
    print("  OK picker has 3 frames + upload + library + skip + cancel")


def test_library_keyboard_has_thumb_buttons_and_navigation():
    """Library kb: N thumb-кнопок + reroll + back."""
    sample = [
        {"id": f"mj_{i:03d}", "path": f"/x/{i}.jpg"} for i in range(6)
    ]
    kb = cover.library_keyboard(sample)
    all_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    pick_data = [d for d in all_data if d.startswith("selfie_cover:lib_pick:")]
    assert len(pick_data) == 6, f"Expected 6 pick buttons, got {len(pick_data)}"
    assert "selfie_cover:lib_reroll" in all_data
    assert "selfie_cover:back" in all_data
    print("  OK library kb: 6 thumbs + reroll + back")


def test_library_keyboard_empty_sample_shows_back_only():
    """Если фото нет — кнопка только Back."""
    kb = cover.library_keyboard([])
    all_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "selfie_cover:back" in all_data
    pick_data = [d for d in all_data if d.startswith("selfie_cover:lib_pick:")]
    assert len(pick_data) == 0
    print("  OK empty sample → only back button")


# ── Messages ────────────────────────────────────────────────────────────────

def test_picker_message_mentions_cover_and_options():
    msg = cover.build_picker_message()
    low = msg.lower()
    assert "обложк" in low, f"No cover mention: {msg!r}"
    print("  OK picker message mentions cover")


def test_upload_prompt_message_clear():
    """Сообщение для state uploading понятно — юзер должен знать что прислать фото."""
    msg = cover.build_upload_prompt_message()
    low = msg.lower()
    assert "фото" in low, f"No photo mention: {msg!r}"
    print("  OK upload prompt clear")


# ── runner ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [(n, fn) for n, fn in globals().items() if n.startswith("test_") and callable(fn)]
    failed = 0
    print(f"\n{'='*70}\nRunning {len(tests)} tests in selfie/tests/test_cover.py\n{'='*70}")
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
