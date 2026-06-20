"""TDD: bot_state.project_dir — fallback на notion_edit_card/notion_edit_title.

Порт B2 (паритет legacy→ядро). Без fallback «Скачать материалы» в режиме
редактирования карточки (pending ставит notion_edit_card, но НЕ notion_page_id)
резолвил None → отдавал только обложку, без файлов проекта/фото. Грабли
content-bot 18 июня; legacy bot.py:712 уже содержит фикс — портируем в ядро.

Запуск: python tests/test_bot_state_project_dir.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows-консоль (charmap) не кодирует кириллицу/стрелки в print → форсим UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import bot_state  # noqa: E402

# project_dir() делает mkdir — уводим в temp, чтобы тест не плодил папки в репо.
bot_state.PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="test_projects_"))

_errs: list[str] = []


def _assert(cond, msg):
    print(f"  {'OK' if cond else 'X FAIL'} {msg}")
    if not cond:
        _errs.append(msg)


def test_normal_notion_page_id():
    print("\n-- обычный режим: notion_page_id резолвит папку --")
    d = bot_state.project_dir({"notion_page_id": "33b0ef6e-aaaa",
                               "card_data": {"title": "Летняя акция"}})
    _assert(d is not None, "папка создана")
    _assert(d is not None and d.name.startswith("33b0ef6e"), "префикс из notion_page_id")
    _assert(d is not None and "Летняя акция" in d.name, "title из card_data")


def test_edit_mode_fallback():
    print("\n-- edit-режим: notion_edit_card без notion_page_id (B2) --")
    d = bot_state.project_dir({"notion_edit_card": "44c1f0a2-bbbb",
                               "notion_edit_title": "Лоферы"})
    _assert(d is not None, "папка резолвится по notion_edit_card (раньше None)")
    _assert(d is not None and d.name.startswith("44c1f0a2"), "префикс из notion_edit_card")
    _assert(d is not None and "Лоферы" in d.name, "title из notion_edit_title")


def test_page_id_wins_over_edit():
    print("\n-- notion_page_id приоритетнее notion_edit_card --")
    d = bot_state.project_dir({"notion_page_id": "11111111-x",
                               "notion_edit_card": "22222222-y"})
    _assert(d is not None and d.name.startswith("11111111"), "page_id выигрывает")


def test_title_fallback_chain():
    print("\n-- title: card_data → notion_edit_title → untitled --")
    d = bot_state.project_dir({"notion_edit_card": "55d2a1b3-z"})
    _assert(d is not None and d.name.endswith("untitled"), "нет title → untitled")


def test_no_ids_returns_none():
    print("\n-- нет ни одного id → None --")
    d = bot_state.project_dir({"card_data": {"title": "x"}})
    _assert(d is None, "без notion_page_id/notion_edit_card → None")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"\n{'='*60}\nRunning {len(tests)} bot_state.project_dir tests\n{'='*60}")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _errs.append(f"{fn.__name__}: {e}")
            print(f"  X EXC {fn.__name__}: {e}")
    print(f"\n{'='*60}")
    print("ALL PASS" if not _errs else f"FAIL ({len(_errs)}): " + "; ".join(_errs))
    sys.exit(0 if not _errs else 1)
