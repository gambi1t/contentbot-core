"""Тест бренд-хэндла в слайдах карусели (10 июня, Артём).

Карусели идут на ЛИЧНЫЙ аккаунт Максима @yumsunov86, а в слайдах был зашит
@livedrive.tmn (картинговый бизнес-аккаунт) — в футере и в subtitle обложки.
_validate_slides должен ДЕТЕРМИНИРОВАННО нормализовать: handle=@yumsunov86 на
всех слайдах + вычистить любой @livedrive.tmn из текстовых полей (на случай,
если LLM/кэш всё равно его выдаст).

Запуск: python tests/test_carousel_handle.py
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

from carousel import llm as carousel_llm  # noqa: E402

WRONG = "@livedrive.tmn"
RIGHT = "@yumsunov86"


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    print("\n[_validate_slides — нормализация хэндла на @yumsunov86]")
    slides = [
        {  # cover
            "template": "M1", "kicker": "16 ЛЕТ", "hero": "5",
            "title_main": "СЕБЕ", "title_accent": "В 24",
            "subtitle": f"что хотел бы услышать · {WRONG}",
            "counter": "01 / 07", "handle": WRONG,
        },
        {  # inner с явным неправильным handle + упоминание в body
            "kicker": "СОВЕТ 01", "title": "СЧИТАЙ",
            "body": f"подпишись {WRONG} — там разборы",
            "counter": "02 / 07", "handle": WRONG,
        },
        {  # inner без handle (путь setdefault)
            "kicker": "СОВЕТ 02", "title": "ЛЮДИ", "body": "контекст",
            "counter": "03 / 07",
        },
    ]
    out = carousel_llm._validate_slides(slides, 3)

    _assert(all(s.get("handle") == RIGHT for s in out),
            f"handle == {RIGHT} на ВСЕХ слайдах (вкл. cover и setdefault), "
            f"got {[s.get('handle') for s in out]}", errors)
    leaked = [
        (i, k) for i, s in enumerate(out) for k, v in s.items()
        if isinstance(v, str) and WRONG in v
    ]
    _assert(not leaked, f"нигде не осталось {WRONG} в текстах, leaked={leaked}", errors)
    _assert(RIGHT in out[0]["subtitle"],
            f"в subtitle обложки {WRONG}→{RIGHT}, got {out[0]['subtitle']!r}", errors)
    _assert(RIGHT in out[1]["body"],
            f"в body внутреннего {WRONG}→{RIGHT}, got {out[1]['body']!r}", errors)

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
