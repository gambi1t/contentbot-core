"""TDD: замена тире НЕ должна оставлять «плавающую» запятую (« , » с пробелом
перед ней). v3 Creative озвучивает плавающий знак как мусорное слово
(«терне»/«тире»/«можно», Артём 31 мая, подтверждено реальной генерацией).

Корень бага: `result.replace("—", ",")` на «зарплаты — каждый» давало
«зарплаты , каждый» (пробел остался ПЕРЕД запятой). Фикс — схлопывать пробелы,
приклеивая запятую к предыдущему слову: «зарплаты, каждый».

Run: python tests/test_tts_no_orphan_comma.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402

_ORPHAN = re.compile(r"\s,")  # пробел перед запятой = плавающая запятая


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_em_dash_attached(errors: list[str]) -> None:
    print("\n-- em-dash → запятая ПРИКЛЕЕНА (v3) --")
    out = bot.transliterate_for_tts("зарплаты — каждый месяц", model_id="eleven_v3")
    _assert("—" not in out, "нет em-dash", errors)
    _assert(not _ORPHAN.search(out), f"нет плавающей « ,» (got: {out!r})", errors)
    _assert("зарплаты, каждый" in out, f"запятая приклеена (got: {out!r})", errors)


def test_en_dash_attached(errors: list[str]) -> None:
    print("\n-- en-dash → запятая ПРИКЛЕЕНА --")
    out = bot.transliterate_for_tts("выручка – окном", model_id="eleven_v3")
    _assert(not _ORPHAN.search(out), f"нет плавающей « ,» (got: {out!r})", errors)
    _assert("выручка, окном" in out, f"запятая приклеена (got: {out!r})", errors)


def test_full_script_no_orphan(errors: list[str]) -> None:
    print("\n-- реальный сценарий 12:53: ни одной плавающей запятой --")
    sample = (
        "Прибыль приходит сезоном. А аренда и зарплаты — каждый месяц! "
        "Резерв в процентах тут не работает.\n"
        "В сезонном бизнесе расходы идут ровно весь год: аренда, зарплаты, "
        "обслуживание. А выручка — окном.\n"
        "Резерв — это статья расходов. Я считаю его от месяцев простоя."
    )
    out = bot.transliterate_for_tts(sample, model_id="eleven_v3")
    _assert("—" not in out, "нет em-dash", errors)
    _assert(not _ORPHAN.search(out), f"нет ни одной « ,» (got: {out!r})", errors)
    # обычные запятые в перечислении не пострадали
    _assert("аренда, зарплаты, обслуживание" in out, "перечисление цело", errors)


def test_spaced_hyphen_attached(errors: list[str]) -> None:
    print("\n-- спейснутый дефис « - » → приклеенная запятая --")
    out = bot.transliterate_for_tts("меняешь логику - и решаешь", model_id="eleven_v3")
    _assert(not _ORPHAN.search(out), f"нет плавающей « ,» (got: {out!r})", errors)


def test_v2_also_no_orphan(errors: list[str]) -> None:
    print("\n-- v2: тоже без плавающей запятой --")
    out = bot.transliterate_for_tts("зарплаты — каждый месяц", model_id="eleven_multilingual_v2")
    _assert(not _ORPHAN.search(out), f"нет плавающей « ,» (got: {out!r})", errors)
    _assert("зарплаты, каждый" in out, "запятая приклеена и в v2", errors)


def main() -> int:
    print("=" * 60)
    print("test_tts_no_orphan_comma")
    print("=" * 60)
    errors: list[str] = []
    test_em_dash_attached(errors)
    test_en_dash_attached(errors)
    test_full_script_no_orphan(errors)
    test_spaced_hyphen_attached(errors)
    test_v2_also_no_orphan(errors)
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
