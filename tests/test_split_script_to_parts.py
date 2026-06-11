"""Regression tests for split_script_to_parts.

Protects against the Apr 15 2026 bug where Claude's intonation pass inserted
paragraph breaks mid-clause and the splitter produced parts ending on a comma
instead of a sentence terminator.

The splitter is a pure function, but bot.py has side effects at import time
(dotenv, API clients, etc.), so we set dummy env vars and import lazily.

Run: python tests/test_split_script_to_parts.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Dummy env so `import bot` doesn't explode on missing API keys.
os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import once, reuse.
import bot  # noqa: E402

split = bot.split_script_to_parts

TERMINATORS = set('.!?»"…')


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    if not cond:
        errors.append(f"FAIL {msg}")
        print(f"  FAIL {msg}")
    else:
        print(f"  OK {msg}")


def _ends_on_terminator(s: str) -> bool:
    return bool(s) and s.rstrip()[-1] in TERMINATORS


def test_empty_input(errors: list[str]) -> None:
    print("\n-- empty input --")
    _assert(split("") == [], "empty string → []", errors)
    _assert(split("   ") == [], "whitespace-only → []", errors)


def test_single_sentence(errors: list[str]) -> None:
    print("\n-- single sentence --")
    text = "Только одно предложение здесь."
    parts = split(text)
    _assert(len(parts) == 1, f"single sentence stays as 1 part (got {len(parts)})", errors)
    _assert(parts[0] == text, "content preserved", errors)


def test_all_parts_end_with_terminator(errors: list[str]) -> None:
    """Every part except possibly the last must end on .!?»"… — no mid-sentence splits."""
    print("\n-- all parts end on terminator --")
    text = (
        "Midjourney 8.1 спасла нейросетку от скучного реализма! "
        "И вернула магию творчества! "
        "После провальной восьмёрки AI снова стал художником, а не фотороботом. "
        "Генерит изображения быстрее при взрывном качестве. "
        "Вернули загрузку картинок и функцию, которая анализирует изображение и выдаёт готовый промпт. "
        "Добавили мудборды для передачи стиля. "
        "Всё работает на старых тарифах! "
        "Подписывайтесь в телеграм, там делюсь AI-инструментами."
    )
    parts = split(text)
    _assert(len(parts) >= 2, f"long text splits into multiple parts (got {len(parts)})", errors)
    for i, p in enumerate(parts):
        _assert(
            _ends_on_terminator(p),
            f"part {i} ends on terminator (tail: {p[-20:]!r})",
            errors,
        )


def test_paragraph_break_mid_clause(errors: list[str]) -> None:
    """Apr 15 2026 regression: Claude put \\n\\n in the middle of a clause.

    The glued-paragraph logic should join them back before splitting.
    """
    print("\n-- paragraph break mid-clause --")
    text = (
        "Midjourney 8.1 спасла нейросетку от скучного реализма! "
        "И вернула магию творчества!\n\n"
        "После провальной восьмёрки AI снова стал художником, а не фотороботом. "
        "Генерит изображения быстрее при взрывном качестве.\n\n"
        "Вернули загрузку картинок и функцию, которая\n\n"  # bad break here
        "анализирует изображение и выдаёт готовый промпт. "
        "Добавили мудборды для передачи стиля.\n\n"
        "Всё работает на старых тарифах! "
        "Подписывайтесь в телеграм, там делюсь AI-инструментами."
    )
    parts = split(text)
    for i, p in enumerate(parts):
        _assert(
            _ends_on_terminator(p),
            f"part {i} ends on terminator despite mid-clause break (tail: {p[-30:]!r})",
            errors,
        )
    # The sentence must survive intact somewhere in the output.
    joined = " ".join(parts)
    _assert(
        "функцию, которая анализирует изображение" in joined,
        "mid-clause sentence survived intact",
        errors,
    )


def test_no_split_on_comma(errors: list[str]) -> None:
    print("\n-- no split on comma --")
    text = (
        "Короткая фраза. "
        "Длинная фраза продолжается и продолжается, и ещё, и снова, "
        "и здесь тоже есть запятая, а вот и конец. "
        "Совсем короткая. Ещё. И ещё. И последняя."
    )
    parts = split(text)
    for i, p in enumerate(parts):
        _assert(
            _ends_on_terminator(p),
            f"part {i} never ends on comma (tail: {p[-20:]!r})",
            errors,
        )


def test_clean_paragraphs(errors: list[str]) -> None:
    print("\n-- clean paragraphs (proper \\n\\n) --")
    text = (
        "Первый абзац состоит из двух фраз. Это вторая фраза первого абзаца.\n\n"
        "Второй абзац тоже из двух фраз. И эта вторая фраза второго.\n\n"
        "Третий абзац здесь. Конец истории."
    )
    parts = split(text)
    for i, p in enumerate(parts):
        _assert(
            _ends_on_terminator(p),
            f"part {i} ends on terminator ({p[-20:]!r})",
            errors,
        )
    # Original content preserved.
    joined = " ".join(parts)
    for needle in [
        "Первый абзац",
        "вторая фраза первого абзаца",
        "Второй абзац",
        "Третий абзац",
        "Конец истории",
    ]:
        _assert(needle in joined, f"content preserved: {needle!r}", errors)


def test_midjourney_raw_script(errors: list[str]) -> None:
    """The exact Midjourney card script that triggered the original bug."""
    print("\n-- midjourney raw script --")
    text = (
        "Midjourney 8.1 спасла нейросетку от скучного реализма — "
        "и вернула магию творчества. После провальной восьмёрки AI снова стал "
        "художником, а не фотороботом. Генерит 2K изображения быстрее при "
        "взрывном качестве. Вернули загрузку картинок и Describe — анализирует "
        "изображение и выдаёт готовый промпт. Добавили мудборды для передачи "
        "стиля. Всё работает на старых тарифах. Подписывайтесь в телеграм, "
        "там делюсь самыми мощными AI-инструментами для креатива."
    )
    parts = split(text)
    _assert(len(parts) == 2, f"auto-scales to 2 parts for ~450 char script (got {len(parts)})", errors)
    for i, p in enumerate(parts):
        _assert(
            _ends_on_terminator(p),
            f"midjourney part {i} ends on terminator ({p[-20:]!r})",
            errors,
        )


def test_target_parts_override(errors: list[str]) -> None:
    print("\n-- explicit target_parts --")
    text = "Один. Два! Три? Четыре. Пять. Шесть. Семь. Восемь."
    for target in (2, 3, 4):
        parts = split(text, target_parts=target)
        _assert(
            len(parts) == target,
            f"target_parts={target} → {target} parts (got {len(parts)})",
            errors,
        )


def test_incomplete_last_fragment_preserved(errors: list[str]) -> None:
    """A trailing fragment without a terminator must not be silently dropped."""
    print("\n-- trailing no-terminator fragment --")
    text = "Первая фраза. Вторая фраза. Обрывок без точки"
    parts = split(text)
    joined = " ".join(parts)
    _assert("Обрывок без точки" in joined, "trailing fragment preserved", errors)


def main() -> int:
    print("=" * 60)
    print("split_script_to_parts regression suite")
    print("=" * 60)

    errors: list[str] = []

    test_empty_input(errors)
    test_single_sentence(errors)
    test_all_parts_end_with_terminator(errors)
    test_paragraph_break_mid_clause(errors)
    test_no_split_on_comma(errors)
    test_clean_paragraphs(errors)
    test_midjourney_raw_script(errors)
    test_target_parts_override(errors)
    test_incomplete_last_fragment_preserved(errors)

    print("\n" + "=" * 60)
    if errors:
        print(f"Found {len(errors)} failure(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK all split_script_to_parts tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
