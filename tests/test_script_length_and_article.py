"""TDD: (B) длина сценария реагирует на правку + (A) полное чтение статьи.

B — _extract_target_chars должен ловить фразы Артёма «в районе 600 символов» и
голое «600 символов» (раньше ловил только около/примерно/до/более). И ветки
_edit_script / _generate_script должны прокидывать разбор длины (реюз рецепта
из _apply_script_instruction), а не безусловно крушить до 500.

A — извлечение статьи (_extract_article_from_html) и Jina-обрезка не должны
резать лонгрид до ~6-8к символов: поднято до ARTICLE_MAX_CHARS.

Запуск: python -m pytest tests/test_script_length_and_article.py -v
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402

SRC = (ROOT / "bot.py").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    """Тело функции от 'def name' до следующего top-level 'def/async def'."""
    i = SRC.find(name)
    assert i != -1, f"нет {name}"
    rest = SRC[i + len(name):]
    nxt = min((p for p in (rest.find("\nasync def "), rest.find("\ndef ")) if p != -1),
              default=len(rest))
    return rest[:nxt]


# ── B: _extract_target_chars ловит фразы Артёма ──────────────────────

def test_extract_v_rayone():
    # «в районе 600 символов» — раньше НЕ ловилось
    r = bot._extract_target_chars("сделай в районе 600 символов")
    assert r is not None, "«в районе 600» не распознано"
    assert r[0] <= 600 <= r[1], f"диапазон не вокруг 600: {r}"


def test_extract_bare_n_symbols():
    # голое «600 символов» / «600 знаков» — раньше НЕ ловилось
    r = bot._extract_target_chars("хочу 600 символов")
    assert r is not None and r[0] <= 600 <= r[1], f"голое «600 символов»: {r}"
    r2 = bot._extract_target_chars("сделай 700 знаков")
    assert r2 is not None and r2[0] <= 700 <= r2[1], f"«700 знаков»: {r2}"


def test_extract_existing_still_works():
    # регрессия: старые фразы по-прежнему работают
    assert bot._extract_target_chars("около 500") == (450, 550)
    assert bot._extract_target_chars("450-500 символов") == (450, 500)
    assert bot._extract_target_chars("более 500 символов") == (500, 700)
    assert bot._extract_target_chars("просто перепиши") is None


def test_user_asked_for_longer():
    assert bot._user_asked_for_longer("сделай длиннее") is True
    assert bot._user_asked_for_longer("убери последнее предложение") is False


# ── B: ветки правок прокидывают длину (реюз рецепта) ─────────────────

def test_edit_script_threads_length():
    body = _fn_body("async def _edit_script")
    assert "_user_asked_for_longer" in body and "_extract_target_chars" in body, (
        "_edit_script не прокидывает разбор длины (реюз рецепта)"
    )
    # больше нет безусловного крушения до 500 (должно быть условным)
    assert "max_chars=700" in body or "target_hi=" in body, (
        "_edit_script не расширяет лимит при просьбе «длиннее»"
    )


def test_generate_script_threads_length():
    body = _fn_body("async def _generate_script")
    assert "_user_asked_for_longer" in body or "_extract_target_chars" in body, (
        "_generate_script не учитывает запрос длины из идеи"
    )


# ── A: статья читается целиком (обрезки подняты) ─────────────────────

def test_article_extractor_not_capped_at_6000():
    # 30 абзацев по ~300 символов = ~9000 — старый код резал до 6000 + 15 абзацев
    paras = "".join(f"<p>Параграф {i:02d} " + ("текст " * 45) + "</p>" for i in range(1, 31))
    html = f"<html><body><article>{paras}</article></body></html>"
    out = bot._extract_article_from_html(html)
    assert len(out) > 6000, f"статья всё ещё обрезана до ~6000: {len(out)}"
    assert "Параграф 25" in out, "поздние абзацы (25+) выброшены — читается только верхушка"


def test_article_max_chars_constant_used():
    # source: главная обрезка Jina больше не [:8000]
    assert "ARTICLE_MAX_CHARS" in SRC, "нет константы лимита статьи"
    assert "resp.text[:8000]" not in SRC, "осталась жёсткая обрезка [:8000]"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
