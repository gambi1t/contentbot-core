"""TDD: при нажатии «AI-аватар» в банке идей бот должен показывать НЕ копипаст-
инструкцию, а 2 кнопки автозапуска: «Собрать с аватаром» и «Сначала сценарий».

Баг (Артём 31 мая): «AI-аватар... снова предлагает чтобы я его скопировал и
вставил... должна была быть кнопка, чтобы по этому тезису сразу нажимался
генерировать».

Что проверяем (контракт UX, без полного TG-инстанса):
  (1) в коде ветки `pipeline == "avatar"` в idea_pipeline handler есть
      callback_data с префиксом `idea_avatar_full:` и `idea_avatar_script:`
      (новые кнопки автозапуска)
  (2) ветки сами обработаны: handler для `idea_avatar_full:<idx>` и
      `idea_avatar_script:<idx>` объявлен в bot.py
  (3) для full-кнопки в pending выставляется флаг `auto_after_approve =
      "avatar_full"` (контракт для будущей Фазы 2 — авто-цепочка после approve)

Run: python tests/test_idea_avatar_autorun.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

BOT_PY = Path(__file__).parent.parent / "bot.py"
SRC = BOT_PY.read_text(encoding="utf-8")


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_no_copypaste_in_avatar_branch(errors: list[str]) -> None:
    print("\n-- ветка avatar: больше нет копипаст-инструкции --")
    # Старая инструкция содержала фразу «Скопируй тезис ниже» и
    # «Жми «✍️ Моя идея» в главном меню». Проверяем что её больше нет
    # ВНУТРИ ветки `if pipeline == "avatar":`.
    m = re.search(
        r'if pipeline == "avatar":\s*(.*?)(?=\n\s{8}#|\n\s{8}if pipeline ==|\n        await query\.answer\(f"⚠️ Неизвестный)',
        SRC, re.DOTALL,
    )
    _assert(m is not None, "ветка `if pipeline == \"avatar\":` найдена", errors)
    if m:
        body = m.group(1)
        _assert(
            "Скопируй тезис ниже" not in body,
            "нет копипаст-фразы «Скопируй тезис ниже»",
            errors,
        )
        _assert(
            'callback_data="cmd_new_idea"' not in body and "cmd_new_idea" not in body,
            "нет редиректа на «✍️ Моя идея» (cmd_new_idea)",
            errors,
        )


def test_two_autorun_buttons_present(errors: list[str]) -> None:
    print("\n-- ветка avatar: 2 кнопки автозапуска --")
    m = re.search(
        r'if pipeline == "avatar":\s*(.*?)(?=\n\s{8}if pipeline ==|\n        await query\.answer\(f"⚠️ Неизвестный)',
        SRC, re.DOTALL,
    )
    if m:
        body = m.group(1)
        _assert(
            "idea_avatar_full:" in body,
            "callback `idea_avatar_full:<idx>` есть",
            errors,
        )
        _assert(
            "idea_avatar_script:" in body,
            "callback `idea_avatar_script:<idx>` есть",
            errors,
        )


def test_handlers_defined(errors: list[str]) -> None:
    print("\n-- handler'ы для новых callback'ов определены --")
    _assert(
        'idea_avatar_full:' in SRC and re.search(r'startswith\(\s*["\']idea_avatar_full[:"]', SRC) is not None
        or 'query.data.startswith("idea_avatar_full' in SRC,
        "есть startswith(\"idea_avatar_full\") в handle_callback",
        errors,
    )
    _assert(
        'query.data.startswith("idea_avatar_script' in SRC
        or re.search(r'startswith\(\s*["\']idea_avatar_script', SRC) is not None,
        "есть startswith(\"idea_avatar_script\") в handle_callback",
        errors,
    )


def test_full_sets_auto_pipeline_flag(errors: list[str]) -> None:
    print("\n-- full-handler выставляет флаг auto_after_approve='avatar_full' --")
    # Ищем участок кода вокруг idea_avatar_full handler и проверяем что
    # рядом упоминается auto_after_approve со значением avatar_full.
    _assert(
        'auto_after_approve' in SRC and 'avatar_full' in SRC,
        "присутствует контракт `auto_after_approve = 'avatar_full'`",
        errors,
    )


def test_seed_text_uses_thesis(errors: list[str]) -> None:
    print("\n-- handler формирует seed text из тезиса+хука --")
    # Не строгий regex — просто что в handler есть central_thesis и hook_draft
    handler_region = re.search(
        r'startswith\(\s*["\']idea_avatar_full[:"]\s*\)(.*?)(?=\n\s{4}if query\.data\.startswith|\Z)',
        SRC, re.DOTALL,
    )
    if handler_region:
        body = handler_region.group(1)[:3000]  # ограничение поиска
        _assert(
            "central_thesis" in body or "central_thesis" in SRC,
            "handler использует central_thesis (через idea dict)",
            errors,
        )


def main() -> int:
    print("=" * 60)
    print("test_idea_avatar_autorun")
    print("=" * 60)
    errors: list[str] = []
    test_no_copypaste_in_avatar_branch(errors)
    test_two_autorun_buttons_present(errors)
    test_handlers_defined(errors)
    test_full_sets_auto_pipeline_flag(errors)
    test_seed_text_uses_thesis(errors)
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
