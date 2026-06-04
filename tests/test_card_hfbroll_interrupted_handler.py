"""TDD: card_hfbroll handler в bot.py должен ловить HyperFramesInterrupted
ОТДЕЛЬНО от общего Exception и показывать retry-кнопку.

Баг (Артём 31 мая): после моего systemd-restart юзер увидел пугающее
«Claude Code упал (rc=143)» в Telegram, без кнопки повтора. Должен видеть
«🔁 Сервис перезапускался» + кнопку «Повторить HyperFrames».

Проверяем (контракт по исходнику — без live-инстанса бота):
  (1) bot.py импортирует HyperFramesInterrupted
  (2) card_hfbroll handler содержит `except HyperFramesInterrupted as`
  (3) сообщение НЕ содержит фразы «Claude Code упал»
  (4) есть retry-кнопка с callback_data, равным текущему query.data

Run: python tests/test_card_hfbroll_interrupted_handler.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")

BOT_PY = Path(__file__).parent.parent / "bot.py"
SRC = BOT_PY.read_text(encoding="utf-8")


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _hfbroll_handler_body() -> str:
    """Извлекает тело card_hfbroll handler (от его if до следующего if)."""
    m = re.search(
        r'if query\.data\.startswith\("card_hfbroll:"\):(.*?)(?=\n    if query\.data\.startswith|\n    if query\.data ==)',
        SRC, re.DOTALL,
    )
    return m.group(1) if m else ""


def test_imports_interrupted_class(errors: list[str]) -> None:
    print("\n-- bot.py импортирует HyperFramesInterrupted --")
    _assert(
        "HyperFramesInterrupted" in SRC,
        "HyperFramesInterrupted упоминается в bot.py",
        errors,
    )


def test_handler_catches_interrupted_separately(errors: list[str]) -> None:
    print("\n-- card_hfbroll ловит HyperFramesInterrupted ОТДЕЛЬНО --")
    body = _hfbroll_handler_body()
    _assert(len(body) > 100, "тело handler'а найдено", errors)
    if body:
        _assert(
            "except HyperFramesInterrupted" in body,
            "есть `except HyperFramesInterrupted`",
            errors,
        )
        _assert(
            "except Exception" in body,
            "есть `except Exception` (общий fallback тоже)",
            errors,
        )
        # Порядок: Interrupted ДО общего Exception (иначе никогда не сработает)
        pos_int = body.find("except HyperFramesInterrupted")
        pos_gen = body.find("except Exception")
        _assert(
            pos_int >= 0 and pos_gen >= 0 and pos_int < pos_gen,
            f"HyperFramesInterrupted раньше Exception (int@{pos_int}, gen@{pos_gen})",
            errors,
        )


def test_friendly_message_no_panic(errors: list[str]) -> None:
    print("\n-- сообщение Interrupted-ветки дружелюбное --")
    body = _hfbroll_handler_body()
    # Найдём блок Interrupted (от `except HyperFramesInterrupted` до следующего `except`)
    m = re.search(
        r'except HyperFramesInterrupted.*?(?=\n\s+except Exception)',
        body, re.DOTALL,
    )
    if m:
        block = m.group(0)
        _assert(
            "Claude Code упал" not in block,
            "нет фразы «Claude Code упал» в Interrupted-ветке",
            errors,
        )
        _assert(
            "перезапус" in block.lower() or "перезапускал" in block.lower(),
            "есть упоминание «перезапус» (контекст для юзера)",
            errors,
        )


def test_retry_button_in_interrupted(errors: list[str]) -> None:
    print("\n-- Interrupted-ветка предлагает retry-кнопку --")
    body = _hfbroll_handler_body()
    m = re.search(
        r'except HyperFramesInterrupted.*?(?=\n\s+except Exception)',
        body, re.DOTALL,
    )
    if m:
        block = m.group(0)
        # Retry = callback_data, равный query.data (тот же card_hfbroll:<id>)
        _assert(
            "callback_data=query.data" in block,
            "есть кнопка с callback_data=query.data (повтор того же действия)",
            errors,
        )
        _assert(
            "Повторить" in block,
            "лейбл «Повторить» на кнопке",
            errors,
        )


def main() -> int:
    print("=" * 60)
    print("test_card_hfbroll_interrupted_handler")
    print("=" * 60)
    errors: list[str] = []
    test_imports_interrupted_class(errors)
    test_handler_catches_interrupted_separately(errors)
    test_friendly_message_no_panic(errors)
    test_retry_button_in_interrupted(errors)
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
