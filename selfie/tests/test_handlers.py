"""Тесты selfie.handlers — helper-функции.

Полный flow (download/transcribe/burn) тестируется через Telethon end-to-end,
здесь — только чистые helper'ы без I/O.

Запуск: python selfie/tests/test_handlers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from selfie.handlers import (  # noqa: E402
    build_review_message,
    truncate_for_preview,
    detect_text_unchanged,
)


# ── truncate_for_preview ────────────────────────────────────────────────────

def test_truncate_short_text_unchanged():
    text = "Короткий текст"
    assert truncate_for_preview(text, 100) == "Короткий текст"
    print("  OK short text unchanged")


def test_truncate_long_text_with_ellipsis():
    text = "a" * 1000
    result = truncate_for_preview(text, 100)
    assert len(result) <= 105, f"Too long: {len(result)}"
    assert result.endswith("…") or result.endswith("..."), f"No ellipsis: {result[-3:]!r}"
    print("  OK long text truncated with ellipsis")


def test_truncate_empty_text():
    assert truncate_for_preview("", 100) == ""
    print("  OK empty text returns empty")


# ── build_review_message ────────────────────────────────────────────────────

def test_review_message_contains_transcription():
    msg = build_review_message("Привет мир")
    assert "Привет мир" in msg, f"Transcription missing from review msg"
    print("  OK contains transcription text")


def test_review_message_has_call_to_action():
    """Сообщение должно явно говорить юзеру что делать дальше."""
    msg = build_review_message("Test")
    # Должна быть подсказка про кнопки
    lower = msg.lower()
    assert "редакт" in lower or "использов" in lower or "кноп" in lower, \
        f"No action hint in: {msg!r}"
    print("  OK has call-to-action hint")


def test_review_message_truncates_very_long_transcription():
    """Длинная транскрипция должна быть обрезана (TG лимит сообщения 4096 chars)."""
    long_text = "слово " * 1000  # ~6000 chars
    msg = build_review_message(long_text)
    assert len(msg) < 4096, f"Message too long for TG: {len(msg)}"
    print("  OK very long transcription truncated")


# ── detect_text_unchanged ────────────────────────────────────────────────────

def test_detect_unchanged_exact_match():
    """Идентичные строки → True."""
    assert detect_text_unchanged("Привет мир", "Привет мир") is True
    print("  OK exact match detected")


def test_detect_unchanged_whitespace_diff():
    """Разный whitespace, но одинаковые слова → True (юзер скопировал/вставил)."""
    assert detect_text_unchanged("Привет  мир", "Привет мир  ") is True
    print("  OK whitespace differences ignored")


def test_detect_unchanged_real_edit_returns_false():
    """Реальная правка → False."""
    assert detect_text_unchanged("Джеминай умеет", "Gemini умеет") is False
    print("  OK real edit returns False")


def test_detect_unchanged_case_sensitive():
    """Регистр учитывается — 'Привет' и 'привет' разные."""
    assert detect_text_unchanged("Привет", "привет") is False
    print("  OK case-sensitive detection")


if __name__ == "__main__":
    print("Running selfie.handlers helper tests:")
    tests = [
        test_truncate_short_text_unchanged,
        test_truncate_long_text_with_ellipsis,
        test_truncate_empty_text,
        test_review_message_contains_transcription,
        test_review_message_has_call_to_action,
        test_review_message_truncates_very_long_transcription,
        test_detect_unchanged_exact_match,
        test_detect_unchanged_whitespace_diff,
        test_detect_unchanged_real_edit_returns_false,
        test_detect_unchanged_case_sensitive,
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
