"""TDD P6b (live-test 30.06): на экране «📰 TG-пост по сценарию» (пайплайн) нет
кнопки точечной правки — только «Опубликовать» и «Перегенерировать».

Фикс: добавить «✏️ Точечная правка (Sonnet)» в _tgpost_script_keyboard + тонкий
флоу (callback tgpost_script_surg:start → state tgpost_script_surg_wait → текст-
ветка в process_idea применяет правку к data['tg_post_from_script']). Реюз
generic _apply_tgpost_surg_edit (brand-aware) — как у idea-flow, но источник/рендер
пайплайновые.

Запуск: python tests/test_tgpost_script_surg_p6b.py
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


def test_surg_button_in_script_keyboard(errors):
    print("\n-- кнопка «Точечная правка» в клавиатуре пайплайн-поста --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    idx = src.find("def _tgpost_script_keyboard")
    _assert(idx != -1, "_tgpost_script_keyboard найдена", errors)
    body = src[idx: idx + 1500]
    _assert('callback_data="tgpost_script_surg:start"' in body,
            "кнопка точечной правки (tgpost_script_surg:start) в _tgpost_script_keyboard", errors)


def test_surg_text_handler_applies_to_pipeline_post(errors):
    print("\n-- process_idea ловит tgpost_script_surg_wait и правит tg_post_from_script --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    _assert('state == "tgpost_script_surg_wait"' in src,
            "process_idea ловит state tgpost_script_surg_wait", errors)
    idx = src.find('state == "tgpost_script_surg_wait"')
    if idx == -1:
        return
    region = src[idx: idx + 2600]
    _assert("tg_post_from_script" in region,
            "правка применяется к data['tg_post_from_script'] (пайплайн-пост)", errors)
    _assert("_apply_tgpost_surg_edit" in region,
            "реюз generic _apply_tgpost_surg_edit в ветке", errors)
    _assert("_tgpost_script_keyboard" in region,
            "результат рендерится клавиатурой пайплайн-поста", errors)


def test_surg_start_and_cancel_callbacks(errors):
    print("\n-- callbacks start/cancel зарегистрированы --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    _assert('query.data == "tgpost_script_surg:start"' in src,
            "callback tgpost_script_surg:start", errors)
    _assert('query.data == "tgpost_script_surg:cancel"' in src,
            "callback tgpost_script_surg:cancel", errors)


def main() -> int:
    errors: list = []
    for fn in (test_surg_button_in_script_keyboard,
               test_surg_text_handler_applies_to_pipeline_post,
               test_surg_start_and_cancel_callbacks):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK P6b tgpost-script-surgical test passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
