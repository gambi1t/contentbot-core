"""TDD (Codex review 30.06): notion_card: оставлял stale card-поля прошлой
карточки при переключении.

Сценарий вреда: работал на карточке A (card_continue ставит card_data=A, описание,
crosspost_selected, фото и т.п.) → открыл B через /cards (notion_card: ставил только
script+page_id, НЕ чистя A) → downstream работает с контекстом A. Конкретно:
project_dir (bot_state.py) берёт card_data['title'] В ПРИОРИТЕТЕ над notion_edit_title
→ папка проекта = {B_id}_{A_title} = НЕ та папка, не те материалы. Тихий баг, хуже
KeyError (мой .get его не лечит — stale card_data присутствует).

Фикс: _hydrate_card_context — при switching (другая карточка) чистит card-scoped
поля прошлой + ставит контекст ЭТОЙ (card_data/notion_url/page_id/script/idea); при
той же — WIP сохраняет. + script на сбое Notion: другая карточка → '', та же → старый.

Запуск: python tests/test_card_context_switch.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402
import bot_state  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def test_switch_clears_stale_and_sets_new(errors):
    print("\n-- переключение A→B: stale-поля A очищены, контекст B выставлен --")
    pu = {
        "notion_page_id": "AAAAAAAA1111", "notion_edit_card": "AAAAAAAA1111",
        "card_data": {"title": "Карточка A", "cta": "A-cta"},
        "description": "описание A", "crosspost_selected": ["youtube"],
        "selfie_tg_photos": ["pA"], "cover_text": "обложка A", "chosen_avatar": "avA.png",
        "script": "сценарий A", "state": "cover_approval", "voice_parts": ["vA"],
    }
    cardB = {"id": "BBBBBBBB2222", "title": "Карточка B", "url": "http://notion/b"}
    bot._hydrate_card_context(pu, "BBBBBBBB2222", cardB, "сценарий B", switching=True)
    # контекст B
    _assert(pu["card_data"] == {"title": "Карточка B"}, "card_data = {title: B} (не A)", errors)
    _assert(pu["notion_page_id"] == "BBBBBBBB2222", "notion_page_id = B", errors)
    _assert(pu["notion_edit_title"] == "Карточка B", "notion_edit_title = B", errors)
    _assert(pu["notion_url"] == "http://notion/b", "notion_url = B", errors)
    _assert(pu["script"] == "сценарий B", "script = B", errors)
    _assert(pu.get("idea") == "сценарий B"[:200], "idea = B script[:200]", errors)
    # stale A очищено
    for k in ("description", "crosspost_selected", "selfie_tg_photos", "cover_text",
              "chosen_avatar", "voice_parts"):
        _assert(k not in pu, f"stale '{k}' от A очищен", errors)
    _assert(pu.get("state") is None, "state сброшен (роутинг A не тащим)", errors)


def test_same_card_preserves_wip(errors):
    print("\n-- та же карточка: WIP сохранён --")
    pu = {
        "notion_page_id": "BBBBBBBB2222", "card_data": {"title": "Карточка B"},
        "selfie_tg_photos": ["pB"], "cover_text": "обложка B", "script": "сценарий B",
    }
    cardB = {"id": "BBBBBBBB2222", "title": "Карточка B", "url": "http://notion/b"}
    bot._hydrate_card_context(pu, "BBBBBBBB2222", cardB, "сценарий B", switching=False)
    _assert(pu["selfie_tg_photos"] == ["pB"], "selfie_tg_photos сохранены (та же карточка)", errors)
    _assert(pu["cover_text"] == "обложка B", "cover_text сохранён", errors)
    _assert(pu["card_data"] == {"title": "Карточка B"}, "card_data = B", errors)


def test_project_dir_uses_new_card_after_switch(errors):
    print("\n-- КОНКРЕТНЫЙ вред Codex: project_dir после переключения = папка B, не A --")
    bot_state.PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="ctx_switch_"))
    pu = {"notion_page_id": "AAAAAAAA1111", "card_data": {"title": "Карточка A"}}
    cardB = {"id": "BBBBBBBB2222", "title": "Карточка B", "url": ""}
    bot._hydrate_card_context(pu, "BBBBBBBB2222", cardB, "", switching=True)
    p = bot_state.project_dir(pu)
    _assert(p is not None and "BBBBBBBB" in p.name, "папка по id B", errors)
    _assert(p is not None and "Карточка B" in p.name, "папка с title B", errors)
    _assert(p is not None and "Карточка A" not in p.name, "НЕ A-title (баг устранён)", errors)


def main() -> int:
    errors: list = []
    for fn in (test_switch_clears_stale_and_sets_new, test_same_card_preserves_wip,
               test_project_dir_uses_new_card_after_switch):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK card-context-switch tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
