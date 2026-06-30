"""TDD P7 (live-test 30.06): кросспостинг → «нет описания для публикации» →
кнопка «⬅️ Назад к выбору площадок» неактивна (ничего не делает).

Корень: хендлер платформ-пикера = `if query.data.startswith("crosspost:")`
(bot.py:17326) — матчит ТОЛЬКО crosspost:{card_id}. А кнопка «Назад к выбору
площадок» шлёт ГОЛЫЙ callback_data="crosspost" (без id) → нет матча → тишина
(не исключение → сеть error_handler не помогает).

Фикс: отдать кнопке crosspost:{card_id} (id лежит в data['crosspost_card_id'],
выставлен при входе в crosspost). ALL-PLACES: все прочие crosspost-кнопки уже
шлют crosspost:{id} — голая была одна.

Запуск: python tests/test_crosspost_back_button_p7.py
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


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def test_back_to_platforms_carries_card_id(errors):
    print("\n-- «Назад к выбору площадок» шлёт crosspost:{id}, не голый crosspost --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    _assert('"⬅️ Назад к выбору площадок", callback_data=f"crosspost:' in src,
            "кнопка «Назад к выбору площадок» несёт card_id (crosspost:{id})", errors)
    # Голый callback_data="crosspost" (без двоеточия+id) = мёртвая кнопка (нет матча
    # у startswith("crosspost:")). Его быть НЕ должно нигде.
    _assert('callback_data="crosspost"' not in src,
            "нет ни одного голого callback_data=\"crosspost\" (все с card_id)", errors)


def main() -> int:
    errors: list = []
    test_back_to_platforms_carries_card_id(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK P7 crosspost-back-button test passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
