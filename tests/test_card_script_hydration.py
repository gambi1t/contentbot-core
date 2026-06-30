"""TDD P2b (live-test 30.06): /cards → карточка → «📝 Описание для публикации»
писал «Нет сценария. Сначала создай сценарий.», хотя в Notion (под заголовком
«Сценарий») он есть.

Корень: notion_card: при открытии карточки клал в pending ТОЛЬКО notion_edit_card
+ notion_edit_title, НЕ гидрировал pending['script']. Потребители (gen_description
20470, broll_shooting_list 19103, озвучка) читают data['script'] из памяти без
Notion-фолбэка → ложно «Нет сценария».

Фикс (единая точка): гидрация сценария из Notion при ОТКРЫТИИ карточки в
notion_card: по образцу card_continue (bot.py:14288). БЕЗУСЛОВНО (вкл. '') —
иначе при переключении карточек в памяти останется сценарий ПРЕДЫДУЩЕЙ. В отличие
от card_continue, notion_card: НЕ блокирует меню на пустом сценарии (карточка
должна открыться всегда). Закрывает все три потребителя одной правкой (все читают
тот же pending[user_id]).

Запуск: python tests/test_card_script_hydration.py
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


def _notion_card_body() -> str:
    """Начало тела хендлера notion_card: (где идёт гидрация, до отрисовки меню)."""
    src = Path(bot.__file__).read_text(encoding="utf-8")
    idx = src.find('if query.data.startswith("notion_card:"):')
    assert idx != -1, "хендлер notion_card: не найден"
    return src[idx: idx + 2400]


def test_hydrates_script_from_notion(errors):
    print("\n-- notion_card: гидрирует сценарий из Notion при открытии --")
    body = _notion_card_body()
    _assert("fetch_notion_page_script" in body,
            "тянет сценарий из Notion при открытии карточки", errors)
    # Гидрация (script+notion_page_id+card_data+...) делегирована хелперу
    # _hydrate_card_context (рефактор Codex 30.06). Поведение — в test_card_context_switch.
    _assert("_hydrate_card_context" in body,
            "гидрация контекста карточки через _hydrate_card_context", errors)


def test_does_not_block_menu_on_empty_script(errors):
    print("\n-- notion_card: НЕ блокирует меню на пустом сценарии (открывает карточку всегда) --")
    body = _notion_card_body()
    # card_continue делает return на пустом сценарии («В карточке нет сценария»);
    # notion_card: ОТКРЫВАЕТ карточку при любом статусе — этой блокировки быть не должно.
    _assert("В карточке нет сценария" not in body,
            "нет раннего return по пустому сценарию (карточка открывается всегда)", errors)


def main() -> int:
    errors: list = []
    for fn in (test_hydrates_script_from_notion, test_does_not_block_menu_on_empty_script):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK all card-script-hydration tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
