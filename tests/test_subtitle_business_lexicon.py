"""Тест бизнес-лексикона в каноникализации субтитров (10 июня).

Прод-баг: в ролике «Себестоимость» Максима субтитр показал «МОРЖА» вместо
«маржа» (Whisper ослышался; в карточном монтаже нет review-шага). Механизм
коррекции уже есть (fix_brand_names, AI-бренды) — расширяем бизнес-лексиконом:
финансовые/нишевые термины Максима + «Life Drive».

Запуск: python tests/test_subtitle_business_lexicon.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from subtitle_burner import fix_brand_names  # noqa: E402


def _w(text, start=0.0, end=0.5):
    return {"word": text, "start": start, "end": end}


def _words(*texts):
    return [_w(t, i * 0.5, i * 0.5 + 0.4) for i, t in enumerate(texts)]


def _texts(words):
    return [w["word"] for w in words]


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[«моржа» → «маржа» — прод-баг ролика «Себестоимость»]")
    _assert(_texts(fix_brand_names(_words("Поэтому", "моржа", "есть")))
            == ["Поэтому", "маржа", "есть"], "моржа → маржа", errors)
    _assert(_texts(fix_brand_names(_words("моржа,")))
            == ["маржа,"], "пунктуация хвоста сохраняется (моржа, → маржа,)", errors)
    _assert(_texts(fix_brand_names(_words("МОРЖА")))
            == ["маржа"], "регистр Whisper не мешает (МОРЖА → маржа)", errors)
    _assert(_texts(fix_brand_names(_words("моржинальность")))
            == ["маржинальность"], "моржинальность → маржинальность", errors)

    print("\n[мульти-токенные склейки]")
    _assert(_texts(fix_brand_names(_words("себе", "стоимость")))
            == ["себестоимость"], "себе + стоимость → себестоимость", errors)
    _assert(_texts(fix_brand_names(_words("кэш", "флоу")))
            == ["кэшфлоу"], "кэш + флоу → кэшфлоу", errors)
    _assert(_texts(fix_brand_names(_words("лайф", "драйв")))
            == ["Life Drive"], "лайф + драйв → Life Drive", errors)

    print("\n[одиночные варианты]")
    _assert(_texts(fix_brand_names(_words("кешфлоу")))
            == ["кэшфлоу"], "кешфлоу → кэшфлоу", errors)
    _assert(_texts(fix_brand_names(_words("глемпинг")))
            == ["глэмпинг"], "глемпинг → глэмпинг", errors)
    _assert(_texts(fix_brand_names(_words("глампинг")))
            == ["глэмпинг"], "глампинг → глэмпинг", errors)

    print("\n[безопасность: обычные слова не трогаем]")
    for sent in (("себе", "дороже"), ("можно", "и", "нужно"), ("морж", "на", "льдине")):
        got = _texts(fix_brand_names(_words(*sent)))
        _assert(got == list(sent), f"{' '.join(sent)} — без изменений", errors)

    print("\n[AI-бренды по-прежнему работают]")
    _assert(_texts(fix_brand_names(_words("меджорни")))
            == ["Midjourney"], "меджорни → Midjourney (регресс)", errors)

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
