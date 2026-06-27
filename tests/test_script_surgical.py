"""TDD: surgical_edit_script — точечная правка сценария (не регенерация).

Sonnet получает сценарий+инструкцию → версия с ТОЛЬКО запрошенной правкой.
No-op (Sonnet вернул то же) → SurgicalNoOp с подсказкой переформулировать.

Запуск: python -m pytest tests/test_script_surgical.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import script_surgical as ss  # noqa: E402


class _FakeClaude:
    """Мок Anthropic-клиента: messages.create(...) → reply-текст."""

    def __init__(self, reply: str):
        self.reply = reply
        self.kw = None
        self.messages = self

    def create(self, **kw):
        self.kw = kw
        return type("R", (), {"content": [type("B", (), {"text": self.reply})()]})()


SCRIPT = "Привет. Сегодня покажу, как ИИ снимает рутину. Это меняет всё."


def test_applies_change_returns_edited():
    edited = "Привет. Сегодня покажу, как ИИ снимает рутину. Это меняет игру."
    out = ss.surgical_edit_script(_FakeClaude(edited), SCRIPT, "всё → игру")
    assert out == edited
    assert out != SCRIPT


def test_uses_sonnet_and_surgical_system():
    fc = _FakeClaude(SCRIPT.replace("рутину", "рутину дня"))
    ss.surgical_edit_script(fc, SCRIPT, "после рутину добавь дня")
    assert fc.kw["model"] == "claude-sonnet-4-6", "должен использовать Sonnet"
    assert "БАЙТ-В-БАЙТ" in fc.kw["system"], "system-промпт не точечный"


def test_noop_raises_with_replace_hint():
    # Sonnet вернул тот же текст → no-op; инструкция replace → подсказка с X
    with pytest.raises(ss.SurgicalNoOp) as e:
        ss.surgical_edit_script(_FakeClaude(SCRIPT), SCRIPT, "слово ЗАВОД поменяй на цех")
    assert "ЗАВОД" in str(e.value), "нет подсказки про не найденную подстроку"


def test_noop_whitespace_insensitive():
    # Только пробелы изменились → считаем no-op
    noisy = SCRIPT.replace(". ", ".  ")  # двойные пробелы
    with pytest.raises(ss.SurgicalNoOp):
        ss.surgical_edit_script(_FakeClaude(noisy), SCRIPT, "убери лишнюю точку")


def test_strips_scenario_prefix():
    edited = "СЦЕНАРИЙ:\nПривет. Сегодня покажу, как ИИ снимает рутину. Это меняет мир."
    out = ss.surgical_edit_script(_FakeClaude(edited), SCRIPT, "всё → мир")
    assert not out.upper().startswith("СЦЕНАРИЙ"), "префикс СЦЕНАРИЙ не срезан"
    assert "меняет мир" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
