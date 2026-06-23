"""Прогресс-сообщения HF показывают РЕАЛЬНОЕ число сцен (не зашитое «6»).

Баг (Артём 23.06): на длинном видео движок делал 9 сцен (фича A, лог
«сцен под длину: 9»), но пользователю показывалось «придумываю раскадровку
(6 разных сцен)» — зашитая строка → выглядело как откат к старому. A прокинул
число в промпты/валидатор/SCENE_FILES, но не в user-facing сообщения.

Run: python tests/test_hf_progress_count.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as hf  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_reflects_actual_count(errors):
    print("\n-- сообщение показывает фактическое число сцен --")
    for n in (5, 8, 9, 10):
        s1, s2 = hf._hf_phase_progress(n)
        _assert(f"{n} разных сцен" in s1, f"Шаг 1/3 содержит «{n} разных сцен»", errors)
        _assert(f"{n} HTML-сцен" in s2, f"Шаг 2/3 содержит «{n} HTML-сцен»", errors)


def test_no_hardcoded_6(errors):
    print("\n-- НЕТ зашитой «6» для нешестисценных роликов (анти-регресс) --")
    s1, s2 = hf._hf_phase_progress(9)
    _assert("6 разных сцен" not in s1 and "6 HTML" not in s2, "для 9 сцен нет «6»", errors)
    # для n=6 «6» легитимна
    s1_6, _ = hf._hf_phase_progress(6)
    _assert("6 разных сцен" in s1_6, "для 6 сцен «6» корректна", errors)


def main():
    print("=" * 60 + "\nHF progress message scene count\n" + "=" * 60)
    errors = []
    for fn in (test_reflects_actual_count, test_no_hardcoded_6):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all hf-progress-count tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
