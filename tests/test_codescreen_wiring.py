"""TDD Ф2: кнопка «💻 Экран кода» (screen_broll, Path B) вшита во все места.

Паритет с card_autobroll: где есть Remotion-моушн-кнопка (меню карточки +
B-roll-flow меню), там же должна быть кнопка экрана кода. Гейтится тем же
флагом remotion (_CALLBACK_FEATURE_MAP). Хендлер зовёт generate_screen_broll.

Селфи-пикер и 50/50-композ с talking-head — ОСОЗНАННО отложены (экран кода =
standalone split-формат, не b-roll-вставка; нужно продуктовое решение).

Запуск: python -m pytest tests/test_codescreen_wiring.py -v
"""
from __future__ import annotations

from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "bot.py").read_text(encoding="utf-8")


def test_feature_map_gates_codescreen_under_remotion():
    assert '"card_codescreen": "remotion"' in SRC, "card_codescreen не гейтится remotion"


def test_handler_exists_and_calls_screen_broll():
    assert 'query.data.startswith("card_codescreen:")' in SRC, "нет хендлера card_codescreen"
    assert "generate_screen_broll" in SRC, "хендлер не зовёт generate_screen_broll"


def test_button_present_in_primary_engine_menus():
    # Паритет в ПЕРВИЧНЫХ меню выбора движка — там же, где card_autobroll:
    #   (1) меню карточки Notion, (2) B-roll-flow меню.
    assert "card_codescreen:{full_id[:20]}" in SRC, "нет кнопки в меню карточки Notion"
    assert "card_codescreen:{_card_id_for_autobroll}" in SRC, "нет кнопки в B-roll-flow меню"
    # Прочие card_autobroll-вхождения — НЕ меню выбора движка, а fallback-кнопки
    # «🎨 Попробовать Remotion» при сбое HyperFrames. Туда экран кода намеренно НЕ
    # добавляем: это like-формат ретрай моушна, а экран кода — другой формат (split).


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v"]))
