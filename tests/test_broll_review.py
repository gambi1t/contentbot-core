"""Тест «Моё выбранное» в ОБОИХ путях (9 июня).

Артём: при сборе B-roll по категориям не было где посмотреть/убрать весь набор
до сохранения. Добавлен обзор: карточный (_broll_review_text_kb) + селфи
(кнопка в picker + selfie_broll:review/rm).

Запуск: python tests/test_broll_review.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402
from selfie import broll_picker as bp  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[КАРТОЧНЫЙ — _broll_review_text_kb]")
    data = {
        "broll_clips": [
            {"path": "/x/a.mp4", "source": "local", "category": "karting"},   # 0 видео
            {"path": "/x/b.jpg", "source": "local", "category": "glamping"},  # 1 фото
            {"path": "/x/c.mp4", "source": "local", "category": "sup"},       # 2 видео
        ],
        "broll_selected": [0, 1, 2],
    }
    txt, kb = bot._broll_review_text_kb(data)
    _assert("Моё выбранное (3)" in txt, f"заголовок с числом: {txt[:40]}", errors)
    _assert("🎞 видео — karting" in txt, "видео karting в списке", errors)
    _assert("📷 фото — glamping" in txt, "фото glamping в списке", errors)
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    # 11 июня 2026: namespace brv_rm (review-слой). Раньше был broll_rm:<gi> —
    # он КОЛЛИДИРОВАЛ с легаси broll_rm:<card_prefix>:<file> (управление
    # файлами проекта): int-парсер review-обработчика стоял раньше и ронял
    # легаси-кнопки ValueError'ом. Имена разнесены.
    _assert("brv_rm:0" in cbs and "brv_rm:1" in cbs and "brv_rm:2" in cbs,
            f"кнопки удаления по глоб. индексам (brv_rm): {cbs}", errors)
    _assert(not any(c.startswith("broll_rm:") for c in cbs),
            "review-слой больше НЕ использует broll_rm (коллизия с легаси)", errors)
    _assert("cbroll_save" in cbs, "есть «Сохранить выбранные» (рабочий cbroll_save)", errors)
    _assert("broll" in cbs, "есть «Добавить ещё»", errors)

    print("\n[КАРТОЧНЫЙ — пусто]")
    t0, k0 = bot._broll_review_text_kb({"broll_clips": [], "broll_selected": []})
    _assert("ничего не выбрано" in t0.lower(), "пустой набор → подсказка", errors)

    print("\n[СЕЛФИ — кнопка «Моё выбранное» в picker]")
    items = [bp.BrollItem(kind="video", source=Path("/x/a.mov"), label="library/c1"),
             bp.BrollItem(kind="image", source=Path("/x/b.jpg"), label="library/p1")]
    pkb = bp.build_picker_keyboard(items)
    pcbs = [b.callback_data for row in pkb.inline_keyboard for b in row]
    _assert("selfie_broll:review" in pcbs, f"кнопка обзора в picker: {pcbs}", errors)
    ptexts = [b.text for row in pkb.inline_keyboard for b in row]
    _assert(any("Моё выбранное (2)" in t for t in ptexts), "счётчик в кнопке обзора", errors)
    # без items — кнопки обзора нет
    pkb0 = bp.build_picker_keyboard([])
    _assert("selfie_broll:review" not in [b.callback_data for row in pkb0.inline_keyboard for b in row],
            "при 0 items кнопки обзора нет", errors)

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
