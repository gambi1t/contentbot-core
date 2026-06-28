"""TDD: кнопка «📷 Прикрепить фото» в vanilla /tgpost.

Проверяем проводку (без сети):
  * _kb_review показывает фото-кнопку (пустую и со счётчиком), callback ведёт
    в bot.py-обработчик tgpost_vanilla_photos (подчёркивание — НЕ ловится
    паттерном ^tgpost: → доходит до общего диспетчера bot.py);
  * обратная совместимость: _kb_review() без аргумента не падает;
  * _publish_to_channel принимает photos и при фото уходит в
    telegram_post_to_channel с HTML-конвертацией caption;
  * bot.py: есть вход в фото-меню (маркер tgphoto_return_tgpost), возврат в
    tgphoto_done с реюзом клавиатуры и восстановлением state, и back-label.

Запуск: python -m pytest tests/test_tgpost_photo_button.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tg_post_handlers as tgph  # noqa: E402

BOT_SRC = (ROOT / "bot.py").read_text(encoding="utf-8")


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _cbs(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# ── _kb_review: кнопка фото есть, callback правильный ───────────────────

def test_kb_review_empty_has_photo_button():
    m = tgph._kb_review(0)
    assert "📷 Прикрепить фото" in _texts(m), "нет пустой фото-кнопки"
    assert "tgpost_vanilla_photos" in _cbs(m), "неверный callback фото-кнопки"


def test_kb_review_count_label():
    m = tgph._kb_review(2)
    assert any("2 выбрано" in t for t in _texts(m)), "счётчик фото не на кнопке"


def test_kb_review_backwards_compatible():
    # старые вызовы без аргумента не должны падать
    m = tgph._kb_review()
    assert "📷 Прикрепить фото" in _texts(m)
    # публикация/отмена/notion остались на месте
    assert "tgpost:publish" in _cbs(m)
    assert "tgpost:notion" in _cbs(m)
    assert "tgpost:cancel" in _cbs(m)


# ── _publish_to_channel: фото-путь через telegram_post_to_channel ───────

def test_publish_accepts_photos():
    sig = inspect.signature(tgph._publish_to_channel)
    assert "photos" in sig.parameters, "_publish_to_channel не принимает photos"


def test_publish_photo_path_uses_helper_and_html():
    src = inspect.getsource(tgph._publish_to_channel)
    assert "telegram_post_to_channel" in src, "фото-путь не реюзает helper"
    assert "<b>" in src, "нет markdown→HTML конвертации caption (** → <b>)"
    # текстовый путь без фото сохранён (parse_mode Markdown)
    assert 'parse_mode="Markdown"' in src, "сломан текстовый путь без фото"


# ── bot.py: вход в фото-меню + возврат + back-label ─────────────────────

def test_bot_has_vanilla_photos_entry():
    assert 'query.data == "tgpost_vanilla_photos"' in BOT_SRC, "нет входа в фото-меню"
    assert 'session_data["tgphoto_return_tgpost"] = True' in BOT_SRC, "не ставит маркер возврата"


def test_bot_tgphoto_done_returns_to_vanilla():
    assert 'if data.get("tgphoto_return_tgpost"):' in BOT_SRC, "нет ветки возврата в tgphoto_done"
    assert "_tgph._kb_review(len(tg_photos))" in BOT_SRC, "не реюзает _kb_review со счётчиком"
    assert 'data["state"] = "tgpost_review"' in BOT_SRC, "не восстанавливает state vanilla-флоу"


def test_bot_back_label_covers_vanilla():
    assert 'data.get("tgphoto_return_tgpost")' in BOT_SRC, "back-label не учитывает vanilla-возврат"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
