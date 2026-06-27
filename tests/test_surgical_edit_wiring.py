"""TDD: кнопка «✂️ Точечная правка» вшита во все меню превью сценария (вариант A).

Меню превью сценария дублировалось в ~9 местах → вынесен единый билдер
`_script_preview_keyboard`, кнопка surgical_edit добавлена в нём ОДИН раз.
Callback surgical_edit → state script_surgical_wait → text/voice хендлеры →
`_apply_surgical_edit` → `surgical_edit_script` (точечная правка, не регенерация).

Запуск: python -m pytest tests/test_surgical_edit_wiring.py -v
"""
from __future__ import annotations

from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "bot.py").read_text(encoding="utf-8")


def test_helper_exists_with_surgical_button():
    assert "def _script_preview_keyboard" in SRC, "нет единого билдера клавиатуры"
    assert 'callback_data="surgical_edit"' in SRC, "нет кнопки точечной правки"


def test_no_duplicated_inline_menus_left():
    # «new_hook» и кнопка surgical — ровно по одному (в билдере). Если >1 у
    # new_hook — остался inline-дубль меню (не заменён на билдер).
    assert SRC.count('callback_data="new_hook"') == 1, "остался inline-дубль меню превью"
    assert SRC.count('callback_data="surgical_edit"') == 1, "кнопка точечной правки не из билдера"


def test_callback_sets_surgical_state():
    assert 'query.data == "surgical_edit"' in SRC, "нет хендлера callback surgical_edit"
    assert '"script_surgical_wait"' in SRC, "нет состояния ожидания инструкции"


def test_noop_rearms_surgical_state():
    # state «script_surgical_wait» ВЫСТАВЛЯЕТСЯ дважды: в callback и в no-op-ветке
    # (восстановление режима — иначе подсказка «переформулируй» не работает).
    assert SRC.count('= "script_surgical_wait"') >= 2, "no-op не восстанавливает surgical-режим"


def test_text_and_voice_handlers_call_apply():
    # _apply_surgical_edit: определение + вызов из text-хендлера + из voice-хендлера
    assert SRC.count("_apply_surgical_edit") >= 3, "точечная правка не подключена в text+voice"
    assert "surgical_edit_script" in SRC, "_apply_surgical_edit не зовёт ядро surgical_edit_script"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
