"""TDD: для ElevenLabs v3 НЕ инжектим SSML <break>-теги.

Баг (Артём 31 мая): v3 не поддерживает <break time="X" /> (это фича v2/turbo)
и озвучивает тег как мусор («премани») на месте паузы. Для v3 паузы держатся
на пунктуации, переносы схлопываются в пробел. Для v2 — <break> как раньше.

Run: python tests/test_tts_v3_no_break_tags.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


SAMPLE = "Прибыль приходит сезоном.\n\nА аренда и зарплаты — каждый месяц.\nРезерв не работает."


def test_v3_no_break(errors: list[str]) -> None:
    print("\n-- v3: без <break>-тегов --")
    out = bot.transliterate_for_tts(SAMPLE, model_id="eleven_v3")
    _assert("<break" not in out, f"v3 → нет <break> (got: {out[:80]!r})", errors)
    _assert("—" not in out, "v3 → тире заменено (нет em-dash)", errors)
    # текст не потерян
    _assert("Резерв не работает" in out, "v3 → весь текст на месте", errors)


def test_v2_keeps_break(errors: list[str]) -> None:
    print("\n-- v2: <break>-теги сохранены --")
    out = bot.transliterate_for_tts(SAMPLE, model_id="eleven_multilingual_v2")
    _assert("<break" in out, "v2 → есть <break> (как раньше)", errors)


def test_default_backward_compat(errors: list[str]) -> None:
    print("\n-- дефолт (model_id=None): обратная совместимость --")
    out = bot.transliterate_for_tts(SAMPLE)
    _assert("<break" in out, "default → <break> (поведение v2, не ломаем)", errors)


def main() -> int:
    print("=" * 60)
    print("test_tts_v3_no_break_tags")
    print("=" * 60)
    errors: list[str] = []
    test_v3_no_break(errors)
    test_v2_keeps_break(errors)
    test_default_backward_compat(errors)
    print()
    if errors:
        print(f"FAIL: {len(errors)} assertion(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
