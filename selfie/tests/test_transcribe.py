"""Тесты selfie.transcribe — обёртка над subtitle_burner.transcribe_words
с автоматическим Whisper prompt biasing (initial_prompt из словаря AI-брендов).

Запуск: python selfie/tests/test_transcribe.py

Контракт:
  - build_whisper_prompt() → str: естественная фраза-контекст для Whisper,
    содержит canonical English-формы ключевых AI-брендов (ChatGPT, Claude, ...).
  - transcribe(audio_path, language="ru") → list[dict]: вызывает
    subtitle_burner.transcribe_words с автогенерируемым initial_prompt.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from selfie.transcribe import build_whisper_prompt, transcribe  # noqa: E402


# ── build_whisper_prompt ────────────────────────────────────────────────────

def test_build_whisper_prompt_returns_string():
    """Возвращает строку (не None, не list)."""
    result = build_whisper_prompt()
    assert isinstance(result, str), f"Expected str, got {type(result).__name__}"
    print("  OK returns string")


def test_build_whisper_prompt_not_empty():
    """Строка непустая, длина разумная (≥50, ≤300 символов)."""
    result = build_whisper_prompt()
    assert len(result) >= 50, f"Too short: {len(result)} chars"
    # Whisper initial_prompt рекомендуется < 250-300 символов чтобы не
    # перегрузить контекст модели.
    assert len(result) <= 300, f"Too long: {len(result)} chars — Whisper hint should be concise"
    print(f"  OK length OK ({len(result)} chars)")


def test_build_whisper_prompt_contains_top_brands():
    """Содержит ключевые AI-бренды которые Артём упоминает в роликах."""
    result = build_whisper_prompt()
    must_have = ["ChatGPT", "Claude", "Gemini", "Cursor", "Midjourney"]
    missing = [b for b in must_have if b not in result]
    assert not missing, f"Missing brands in prompt: {missing}"
    print(f"  OK contains all top brands: {must_have}")


def test_build_whisper_prompt_only_canonical_english_forms():
    """В prompt только canonical английские названия — НЕ варианты
    кириллических искажений (которые лежат в словаре для канонизации
    output'а, не для biasing input'а)."""
    result = build_whisper_prompt()
    russian_distortions = ["Джеминай", "Меджорни", "Хейген", "Кьюрсор", "Опенай"]
    found = [d for d in russian_distortions if d in result]
    assert not found, f"Prompt should contain canonical English only, found distortions: {found}"
    print("  OK no Russian distortions in prompt")


# ── transcribe ───────────────────────────────────────────────────────────────

def test_transcribe_passes_initial_prompt_to_underlying():
    """selfie.transcribe вызывает subtitle_burner.transcribe_words с initial_prompt."""
    fake_words = [{"word": "test", "start": 0, "end": 1.0}]
    with mock.patch("selfie.transcribe._transcribe_words", return_value=fake_words) as m:
        result = transcribe("/fake/audio.wav")
    assert m.called, "Underlying transcribe_words not called"
    call_kwargs = m.call_args.kwargs
    call_args = m.call_args.args
    # initial_prompt может быть передан как kwarg или как 4-й positional
    passed_prompt = call_kwargs.get("initial_prompt")
    assert passed_prompt is not None, f"initial_prompt not passed, kwargs={call_kwargs} args={call_args}"
    assert isinstance(passed_prompt, str)
    assert len(passed_prompt) > 0, "Empty initial_prompt passed"
    assert "ChatGPT" in passed_prompt, "Brand biasing not active"
    print("  OK initial_prompt passed to underlying")


def test_transcribe_default_language_ru():
    """По умолчанию language='ru'."""
    fake_words = [{"word": "test", "start": 0, "end": 1}]
    with mock.patch("selfie.transcribe._transcribe_words", return_value=fake_words) as m:
        transcribe("/fake/audio.wav")
    call_kwargs = m.call_args.kwargs
    # language может быть kwarg или 2nd positional
    if "language" in call_kwargs:
        assert call_kwargs["language"] == "ru"
    else:
        # 2nd positional after audio_path
        assert len(m.call_args.args) >= 2 and m.call_args.args[1] == "ru"
    print("  OK default language = ru")


def test_transcribe_custom_language_passed():
    """Кастомный language прокидывается."""
    fake_words = []
    with mock.patch("selfie.transcribe._transcribe_words", return_value=fake_words) as m:
        transcribe("/fake/audio.wav", language="en")
    call_kwargs = m.call_args.kwargs
    if "language" in call_kwargs:
        assert call_kwargs["language"] == "en"
    else:
        assert m.call_args.args[1] == "en"
    print("  OK custom language passed")


def test_transcribe_returns_words_from_underlying():
    """То что вернул transcribe_words — то возвращаем мы (passthrough)."""
    fake_words = [
        {"word": "Привет", "start": 0, "end": 0.5},
        {"word": "Claude", "start": 0.6, "end": 1.0},
    ]
    with mock.patch("selfie.transcribe._transcribe_words", return_value=fake_words):
        result = transcribe("/fake/audio.wav")
    assert result == fake_words
    print("  OK returns words from underlying")


if __name__ == "__main__":
    print("Running selfie.transcribe tests:")
    tests = [
        test_build_whisper_prompt_returns_string,
        test_build_whisper_prompt_not_empty,
        test_build_whisper_prompt_contains_top_brands,
        test_build_whisper_prompt_only_canonical_english_forms,
        test_transcribe_passes_initial_prompt_to_underlying,
        test_transcribe_default_language_ru,
        test_transcribe_custom_language_passed,
        test_transcribe_returns_words_from_underlying,
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
