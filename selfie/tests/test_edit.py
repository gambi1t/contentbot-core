"""Тесты selfie.edit.apply_user_edits — word-by-word замена с сохранением timestamps.

Запуск: python selfie/tests/test_edit.py

Контракт apply_user_edits(orig_words, new_text) → (new_words, warning):
  - Если кол-во слов в new_text == len(orig_words) → заменяем .word в позициях,
    timestamps сохраняем 1:1, warning = None.
  - Если кол-во не совпало → возвращаем (orig_words, warning-строка) —
    оригинал не трогаем, бот покажет warning юзеру.
  - Edge: пустой orig + пустой new_text → ([], None).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add parent dir to sys.path so we can import selfie.edit
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from selfie.edit import apply_user_edits  # noqa: E402


def _w(word, start, end):
    """Shorthand для построения word dict."""
    return {"word": word, "start": start, "end": end}


def test_happy_path_single_word_change():
    """Артём исправил одно слово — остальные не тронуты, timestamps сохранены."""
    orig = [
        _w("Джеминай", 0.0, 0.6),
        _w("умеет", 0.7, 1.0),
        _w("писать", 1.1, 1.5),
    ]
    new_text = "Gemini умеет писать"
    new_words, warning = apply_user_edits(orig, new_text)

    assert warning is None, f"Expected no warning, got: {warning!r}"
    assert len(new_words) == 3, f"Expected 3 words, got {len(new_words)}"
    assert new_words[0]["word"] == "Gemini", f"Word 0: {new_words[0]['word']!r}"
    assert new_words[1]["word"] == "умеет"
    assert new_words[2]["word"] == "писать"
    # Timestamps preserved
    assert new_words[0]["start"] == 0.0
    assert new_words[0]["end"] == 0.6
    assert new_words[1]["start"] == 0.7
    assert new_words[2]["end"] == 1.5
    print("  OK happy path single word change")


def test_no_changes_returns_identical():
    """Если new_text == old text → ничего не меняется."""
    orig = [_w("Привет", 0.0, 0.5), _w("мир", 0.6, 1.0)]
    new_words, warning = apply_user_edits(orig, "Привет мир")
    assert warning is None
    assert [w["word"] for w in new_words] == ["Привет", "мир"]
    assert new_words[0]["start"] == 0.0
    print("  OK no changes returns identical")


def test_multiple_word_changes():
    """Несколько слов заменено одновременно."""
    orig = [
        _w("Кьюрсор", 0.0, 0.5),
        _w("и", 0.6, 0.7),
        _w("Меджорни", 0.8, 1.3),
    ]
    new_words, warning = apply_user_edits(orig, "Cursor и Midjourney")
    assert warning is None
    assert [w["word"] for w in new_words] == ["Cursor", "и", "Midjourney"]
    print("  OK multiple word changes")


def test_word_count_mismatch_fewer_returns_warning():
    """Юзер удалил слово — возвращаем warning, не трогаем оригинал."""
    orig = [
        _w("Это", 0.0, 0.3),
        _w("длинная", 0.4, 0.9),
        _w("фраза", 1.0, 1.5),
        _w("здесь", 1.6, 2.0),
        _w("точно", 2.1, 2.5),
    ]
    new_text = "Это фраза здесь точно"  # 4 слова вместо 5
    new_words, warning = apply_user_edits(orig, new_text)
    assert warning is not None, "Expected warning for word count mismatch"
    assert "5" in warning and "4" in warning, f"Warning should mention counts: {warning!r}"
    # Original untouched
    assert [w["word"] for w in new_words] == ["Это", "длинная", "фраза", "здесь", "точно"]
    print("  OK fewer words returns warning + original")


def test_word_count_mismatch_more_returns_warning():
    """Юзер добавил слово — возвращаем warning."""
    orig = [_w("Привет", 0.0, 0.5), _w("мир", 0.6, 1.0)]
    new_text = "Привет большой мир"  # 3 слова вместо 2
    new_words, warning = apply_user_edits(orig, new_text)
    assert warning is not None
    assert "2" in warning and "3" in warning
    assert [w["word"] for w in new_words] == ["Привет", "мир"]  # original
    print("  OK more words returns warning + original")


def test_punctuation_preserved_in_tokens():
    """Whisper токены с trailing punctuation: 'привет,' одно слово в orig
    и одно слово в new — должно совпасть."""
    orig = [_w("привет,", 0.0, 0.5), _w("мир.", 0.6, 1.0)]
    new_words, warning = apply_user_edits(orig, "hello, world.")
    assert warning is None
    assert [w["word"] for w in new_words] == ["hello,", "world."]
    print("  OK punctuation tokens count correctly")


def test_empty_inputs_returns_empty():
    """Пустой orig + пустой текст → ([], None)."""
    new_words, warning = apply_user_edits([], "")
    assert warning is None
    assert new_words == []
    print("  OK empty inputs return empty")


def test_empty_new_text_against_filled_orig_warning():
    """Юзер прислал пустой текст вместо orig из 3 слов → warning."""
    orig = [_w("Один", 0, 0.5), _w("два", 0.6, 1.0), _w("три", 1.1, 1.5)]
    new_words, warning = apply_user_edits(orig, "")
    assert warning is not None
    assert "3" in warning and "0" in warning
    assert [w["word"] for w in new_words] == ["Один", "два", "три"]
    print("  OK empty new_text returns warning + original")


def test_extra_whitespace_normalized():
    """Двойные/leading/trailing пробелы в new_text не должны ломать count."""
    orig = [_w("a", 0, 0.5), _w("b", 0.6, 1.0)]
    new_words, warning = apply_user_edits(orig, "   x    y   ")  # extra spaces
    assert warning is None, f"Expected no warning, got: {warning!r}"
    assert [w["word"] for w in new_words] == ["x", "y"]
    print("  OK extra whitespace normalized")


def test_orig_words_not_mutated():
    """apply_user_edits должна вернуть НОВЫЙ список, не мутировать orig."""
    orig = [_w("Old", 0, 0.5)]
    orig_snapshot = [dict(w) for w in orig]
    new_words, _ = apply_user_edits(orig, "New")
    assert orig == orig_snapshot, "orig_words was mutated!"
    assert new_words is not orig, "Should return a new list, not the same reference"
    print("  OK orig_words not mutated")


def test_warning_for_mismatch_returns_copy_not_reference():
    """Даже при warning возвращаем КОПИЮ orig (защита от случайной мутации в caller)."""
    orig = [_w("a", 0, 0.5), _w("b", 0.6, 1.0)]
    new_words, warning = apply_user_edits(orig, "c d e")  # mismatch
    assert warning is not None
    assert new_words is not orig
    # Mutating returned should not affect orig
    new_words[0]["word"] = "MUTATED"
    assert orig[0]["word"] == "a", "orig was mutated through returned ref!"
    print("  OK warning path returns copy not reference")


if __name__ == "__main__":
    print("Running selfie.edit.apply_user_edits tests:")
    tests = [
        test_happy_path_single_word_change,
        test_no_changes_returns_identical,
        test_multiple_word_changes,
        test_word_count_mismatch_fewer_returns_warning,
        test_word_count_mismatch_more_returns_warning,
        test_punctuation_preserved_in_tokens,
        test_empty_inputs_returns_empty,
        test_empty_new_text_against_filled_orig_warning,
        test_extra_whitespace_normalized,
        test_orig_words_not_mutated,
        test_warning_for_mismatch_returns_copy_not_reference,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"  FAIL {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print()
    if failed == 0:
        print(f"OK All {len(tests)} tests passed!")
        sys.exit(0)
    else:
        print(f"FAIL {failed}/{len(tests)} tests failed")
        sys.exit(1)
