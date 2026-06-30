"""TDD P3-обложка (Артём 30.06): (1) «🖼 Обложка: «»» пусто; (2) нельзя добавить
букву (только регенерация); (3) «отмена» в cover_approval уходит в текст обложки.

Фикс:
(1) _cover_caption(title): пусто → «без текста (само фото)», не пустые кавычки.
    Применён к 3 легаси-подписям (cover_pick / cover_confirm / cover_ok).
(2) Кнопка «✏️ Написать свой текст» (cover_write_text) → чистое состояние
    cover_edit_waiting (без substring-trap) → любой текст = новый текст обложки.
    Реюз desc_edit-паттерна. cover_notext «Добавить текст» теперь тоже сюда.
(3) В cover_approval «отмена/стоп/cancel» = отмена, не текст обложки (Codex low).

Запуск: python tests/test_cover_text_edit_p3.py
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


def test_cover_caption_empty_not_quotes(errors):
    print("\n-- (1) _cover_caption: пусто → «без текста», не «» --")
    _assert(bot._cover_caption("Промпт за 2990") == "🖼 Обложка: «Промпт за 2990»",
            "непустой текст в кавычках", errors)
    for empty in ("", "   ", None):
        cap = bot._cover_caption(empty)
        _assert("«»" not in cap and "без текста" in cap,
                f"пусто ({empty!r}) → «без текста», не «»", errors)


def test_caption_sites_use_builder(errors):
    print("\n-- (1) 3 легаси-подписи идут через _cover_caption --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    _assert(src.count("_cover_caption(cover_text)") >= 3,
            "≥3 подписи (cover_pick/cover_confirm/cover_ok) через _cover_caption", errors)
    # старая голая подпись с пустыми кавычками убрана из легаси
    _assert('f"🖼 Обложка: «{cover_text}»' not in src,
            "нет голой f-подписи «{cover_text}» (источник пустых «»)", errors)


def test_write_text_button_and_state(errors):
    print("\n-- (2) кнопка «Написать свой текст» + состояние cover_edit_waiting --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    _assert('callback_data="cover_write_text"' in src,
            "кнопка cover_write_text есть", errors)
    _assert('query.data == "cover_write_text"' in src,
            "callback cover_write_text обработан", errors)
    _assert('"cover_edit_waiting"' in src,
            "состояние cover_edit_waiting обрабатывается в process_idea", errors)
    # cover_notext «Добавить текст» теперь ведёт на write, не на регенерацию
    _assert('"✏️ Добавить текст", callback_data="cover_write_text"' in src,
            "cover_notext «Добавить текст» → cover_write_text (ввод, не регенерация)", errors)


def test_otmena_cancels_in_cover_approval(errors):
    print("\n-- (3) «отмена» в cover_approval = отмена, не текст обложки --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    idx = src.find('state") == "cover_approval"')
    _assert(idx != -1, "обработчик cover_approval найден", errors)
    if idx == -1:
        return
    # до установки cover_text должна быть проверка на «отмена»
    set_idx = src.find('["cover_text"] = idea_text', idx)
    region = src[idx: set_idx if set_idx != -1 else idx + 800]
    _assert('"отмена"' in region and ("cancel" in region.lower() or "Отменено" in region),
            "проверка «отмена» ДО установки cover_text", errors)


def main() -> int:
    errors: list = []
    for fn in (test_cover_caption_empty_not_quotes, test_caption_sites_use_builder,
               test_write_text_button_and_state, test_otmena_cancels_in_cover_approval):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK cover-text-edit P3 tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
