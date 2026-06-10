"""Тест позиции субтитров (10 июня).

Фидбэк со встречи с Максимом (IG «За год у меня легло 5 двигателей»):
при MarginV=480 (~75% высоты) слово ложится НА ЛИЦО в близком селфи
(проверено по кадрам 3с/12с). Требование Артёма: «субтитры пониже везде».

Новое правило (визуально подобрано по кадрам реального рендера 10 июня):
- обычные лейауты (селфи/fullscreen/avatar_full/broll_full) → MarginV=300
  (~84% высоты, ниже подбородка в близком кадре);
- split → MarginV=150: нижняя половина = крупный half-кроп головы
  (подбородок ~86% высоты), 300 попадает на губы (кадр «24»), 900-стык
  после подъёма аватара = лоб.

Запуск: python tests/test_subtitle_adaptive.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import subtitle_burner as sb  # noqa: E402

LOW = 300        # обычные лейауты
SPLIT_LOW = 150  # split — ещё ниже (под подбородком half-кропа)


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    plan = [
        {"start": 0.0, "end": 2.5, "layout": "avatar_full"},
        {"start": 2.5, "end": 8.0, "layout": "split"},
        {"start": 8.0, "end": 12.0, "layout": "broll_full"},
        {"start": 12.0, "end": 15.0, "layout": "avatar_full"},
    ]

    print("\n[константы — низкая позиция, не на лице]")
    _assert(sb.DEFAULT_MARGIN_V == LOW,
            f"DEFAULT_MARGIN_V == {LOW}, got {sb.DEFAULT_MARGIN_V}", errors)
    _assert(sb.SPLIT_MARGIN_V == SPLIT_LOW,
            f"SPLIT_MARGIN_V == {SPLIT_LOW}, got {sb.SPLIT_MARGIN_V}", errors)

    print("\n[_margin_for_word — низ во всех лейаутах, split ниже всех]")
    _assert(sb._margin_for_word(1.0, plan) == LOW, f"avatar_full → низ ({LOW})", errors)
    _assert(sb._margin_for_word(5.0, plan) == SPLIT_LOW,
            f"split → под подбородком ({SPLIT_LOW}), не стык 900, не {LOW} (губы)", errors)
    _assert(sb._margin_for_word(9.0, plan) == LOW, f"broll_full → низ ({LOW})", errors)
    _assert(sb._margin_for_word(13.0, plan) == LOW, f"avatar_full (CTA) → низ ({LOW})", errors)
    _assert(sb._margin_for_word(5.0, None) == 0, "без плана → стиль по умолчанию (0)", errors)

    print("\n[generate_ass — нигде нет старых 900/480]")
    words = [
        {"word": "привет", "start": 1.0, "end": 1.4},
        {"word": "смотри", "start": 5.0, "end": 5.4},   # split-слово
        {"word": "это", "start": 9.0, "end": 9.3},
    ]
    out = Path(tempfile.mkdtemp()) / "t.ass"
    sb.generate_ass(words, out, montage_plan=plan)
    text = out.read_text(encoding="utf-8")
    _assert(",900," not in text, "в ASS НЕТ MarginV=900 (стык отменён)", errors)
    _assert(",480," not in text, "в ASS НЕТ MarginV=480 (старый низ отменён)", errors)
    _assert(f",{LOW}," in text, f"в ASS есть MarginV={LOW} (avatar/broll слова)", errors)
    _assert(f",{SPLIT_LOW}," in text, f"в ASS есть MarginV={SPLIT_LOW} (split-слово)", errors)

    print("\n[add_subtitles_to_video — принимает готовые words]")
    import inspect
    sig = inspect.signature(sb.add_subtitles_to_video)
    _assert("words" in sig.parameters, "у add_subtitles_to_video есть параметр words", errors)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
