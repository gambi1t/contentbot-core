"""Раннее сохранение селфи: карточка Notion создаётся на «ОК после расшифровки»
(как «комбайн»/Pipeline-2), а финал её ОБНОВЛЯЕТ, а не плодит дубль (Артём 22.06:
«моё прошлое селфи нигде не сохраняется... на сценарии должна стоять карточка»).

Закрепляем чистую логику bot.py:
- _selfie_first_sentence_title — placeholder-заголовок черновика.
- _selfie_card_data — brand-aware card_data (черновик == финал).
- _selfie_make_draft — create + статус «Монтаж».
- _selfie_persist_card — КЛЮЧЕВОЕ: черновик есть → notion.pages.update (без дубля);
  нет → create_notion_card.
- _selfie_create_draft_card — идемпотентность + best-effort.

Notion-I/O мочим. Run: python tests/test_selfie_draft_card.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def _stub_brand():
    bot._get_active_brand = MagicMock(return_value={
        "telegram_channel_handle": "@yumsunov_realbiz",
        "telegram_channel_display": "Юмсунов | Про реальный бизнес",
    })
    bot._default_platforms = MagicMock(return_value=["TG", "YT"])


# ── placeholder title ─────────────────────────────────────────────────────────

def test_first_sentence_title(errors):
    print("\n-- placeholder-заголовок: первое предложение / фолбэки --")
    _assert(bot._selfie_first_sentence_title("Привет мир. Второе.") == "Привет мир", "первое предложение", errors)
    _assert(bot._selfie_first_sentence_title("") == "Живое видео", "пусто → фолбэк", errors)
    long = "а" * 200
    _assert(len(bot._selfie_first_sentence_title(long)) <= 80, "≤80 символов", errors)


def test_card_data_shape(errors):
    print("\n-- card_data: форма + brand-aware CTA --")
    _stub_brand()
    cd = bot._selfie_card_data("Заголовок")
    _assert(cd["title"] == "Заголовок", "title", errors)
    _assert(cd["rubric"] == "Свободный формат", "rubric", errors)
    _assert(cd["format"] == ["Short video"], "format", errors)
    _assert(cd["platforms"] == ["TG", "YT"], "platforms из brand", errors)
    _assert("@yumsunov_realbiz" in cd["cta"], "CTA с каналом бренда", errors)


# ── make draft ────────────────────────────────────────────────────────────────

def test_make_draft_creates_and_sets_status(errors):
    print("\n-- make_draft: create + статус «Монтаж» --")
    _stub_brand()
    bot.create_notion_card = MagicMock(return_value=("u", "pid"))
    bot.update_notion_status = MagicMock()
    url, pid, cd = bot._selfie_make_draft("Привет мир. Это тест.")
    _assert(bot.create_notion_card.called, "create_notion_card вызван", errors)
    _assert(pid == "pid", "вернул page_id", errors)
    _assert(bot.update_notion_status.call_args.args == ("pid", "Монтаж"), "статус «Монтаж»", errors)
    _assert(cd["title"].startswith("Привет"), "заголовок из первого предложения", errors)


# ── persist: update-vs-create (КЛЮЧЕВОЕ — без дублей) ─────────────────────────

def test_persist_updates_existing_draft(errors):
    print("\n-- persist: черновик есть → UPDATE, БЕЗ create (нет дубля) --")
    _stub_brand()
    bot.create_notion_card = MagicMock(return_value=("NEW", "NEWPID"))
    bot.notion = MagicMock()
    data = {"notion_page_id": "draftpid", "notion_url": "drafturl",
            "card_data": {"title": "старый", "cta": "", "rubric": "Свободный формат",
                          "platforms": ["X"], "format": ["Short video"]}}
    url, pid, cd = bot._selfie_persist_card(data, "Новый Заголовок", "транскрипт", "http://cover")
    _assert(not bot.create_notion_card.called, "create_notion_card НЕ вызван (нет дубля)", errors)
    _assert(pid == "draftpid", "вернул id черновика", errors)
    _assert(url == "drafturl", "вернул url черновика", errors)
    _assert(cd["title"] == "Новый Заголовок", "title обновлён на выбранный хук", errors)
    _assert(bot.notion.pages.update.call_count == 2, "update ×2 (Name + cover)", errors)


def test_persist_creates_when_no_draft(errors):
    print("\n-- persist: черновика нет (напр. /ready) → CREATE --")
    _stub_brand()
    bot.create_notion_card = MagicMock(return_value=("NEWURL", "NEWPID"))
    bot.notion = MagicMock()
    url, pid, cd = bot._selfie_persist_card({}, "Заголовок", "транскрипт", None)
    _assert(bot.create_notion_card.called, "create_notion_card вызван", errors)
    _assert(pid == "NEWPID", "вернул новый id", errors)
    _assert(not bot.notion.pages.update.called, "pages.update НЕ вызван (это create-путь)", errors)


# ── async draft creator: идемпотентность + best-effort ───────────────────────

def test_create_draft_idempotent(errors):
    print("\n-- create_draft_card: карточка уже есть → idempotent (не создаёт) --")
    bot.create_notion_card = MagicMock()
    uid = 999001
    bot.pending[uid] = {"notion_page_id": "exists", "selfie_transcript": "x"}
    asyncio.run(bot._selfie_create_draft_card(MagicMock(), MagicMock(), uid))
    _assert(not bot.create_notion_card.called, "не создаёт второй раз", errors)
    bot.pending.pop(uid, None)


def test_create_draft_stores_ids(errors):
    print("\n-- create_draft_card: создаёт + кладёт ids в pending --")
    _stub_brand()
    bot.create_notion_card = MagicMock(return_value=("u2", "pid2"))
    bot.update_notion_status = MagicMock()
    bot._save_pending = MagicMock()  # без записи на диск
    uid = 999002
    bot.pending[uid] = {"selfie_transcript": "Привет мир. Тест."}
    asyncio.run(bot._selfie_create_draft_card(MagicMock(), MagicMock(), uid))
    e = bot.pending.get(uid, {})
    _assert(e.get("notion_page_id") == "pid2", "notion_page_id в pending", errors)
    _assert(e.get("selfie_draft_card") is True, "флаг selfie_draft_card", errors)
    bot.pending.pop(uid, None)


def test_create_draft_best_effort(errors):
    print("\n-- create_draft_card: ошибка Notion НЕ рвёт flow --")
    bot.create_notion_card = MagicMock(side_effect=RuntimeError("notion down"))
    bot.update_notion_status = MagicMock()
    bot._save_pending = MagicMock()
    _stub_brand()
    uid = 999003
    bot.pending[uid] = {"selfie_transcript": "Привет мир. Тест."}
    try:
        asyncio.run(bot._selfie_create_draft_card(MagicMock(), MagicMock(), uid))
        _assert(bot.pending[uid].get("notion_page_id") is None, "карточки нет, но не упали", errors)
    except Exception as ex:
        _assert(False, f"бросил {ex}", errors)
    bot.pending.pop(uid, None)


def main() -> int:
    print("=" * 60 + "\nselfie draft card (раннее сохранение + update-not-duplicate)\n" + "=" * 60)
    errors: list = []
    for fn in (test_first_sentence_title, test_card_data_shape,
               test_make_draft_creates_and_sets_status,
               test_persist_updates_existing_draft, test_persist_creates_when_no_draft,
               test_create_draft_idempotent, test_create_draft_stores_ids,
               test_create_draft_best_effort):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all selfie-draft-card tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
