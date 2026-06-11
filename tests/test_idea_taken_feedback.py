"""Тест фидбек-лупа банка идей — блок «ЗАШЛО» (11 июня).

Фидбек Максима через Артёма: «не все идеи нравятся». Диагноз 10 июня:
генератор не видит, ЧТО Максим реально берёт — взятые карточки шли в промпт
только как негативный дедуп («УЖЕ БЫЛО»). Фаза A: свежие ВЗЯТЫЕ карточки
(title+rubric из Notion, уже загружаются) идут отдельным ПОЗИТИВНЫМ блоком
вкуса — «улавливай углы/тон, но не дублируй темы».

Запуск: python tests/test_idea_taken_feedback.py
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import idea_generator as ig  # noqa: E402


class _FakeClaude:
    """Захватывает промпт; возвращает валидный JSON-массив из n идей."""
    def __init__(self):
        self.last_user_msg = None
        outer = self

        class _M:
            def create(s, *, model, max_tokens, system, messages, **kw):
                outer.last_user_msg = messages[0]["content"]
                ideas = [{"title": f"Идея {i}", "hook_draft": f"Хук {i}",
                          "central_thesis": f"Тезис {i}"} for i in range(5)]

                class _R:
                    content = [type("C", (), {"text": json.dumps(ideas, ensure_ascii=False)})()]
                return _R()
        self.messages = _M()


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[_build_taken_block — формирование блока]")
    _assert(hasattr(ig, "_build_taken_block"), "функция _build_taken_block есть", errors)
    if not hasattr(ig, "_build_taken_block"):
        print("\n❌ FAIL — нет функции"); return 1

    _assert(ig._build_taken_block([]) == "", "пустой список → пустая строка", errors)
    _assert(ig._build_taken_block([{"title": ""}, {"rubric": "X"}]) == "",
            "без заголовков → пустая строка", errors)

    block = ig._build_taken_block([
        {"title": "Делегирование решения", "rubric": "Виральный ролик"},
        {"title": "Кэшфлоу зимой", "rubric": ""},
    ])
    _assert("ЗАШЛО" in block or "ВЗЯЛ" in block, "блок маркирован как сигнал вкуса", errors)
    _assert("Делегирование решения" in block and "Кэшфлоу зимой" in block,
            "оба заголовка в блоке", errors)
    _assert("Виральный ролик" in block, "рубрика отображается", errors)
    low = block.lower()
    _assert("не дублируй" in low or "не повторяй" in low,
            "явная оговорка «вкус, не копирование»", errors)

    many = [{"title": f"Тема {i}"} for i in range(40)]
    big = ig._build_taken_block(many)
    _assert(big.count("- Тема") <= 15, f"кап ≤15 примеров, got {big.count('- Тема')}", errors)

    print("\n[generate_ideas — параметр и попадание в промпт]")
    sig = inspect.signature(ig.generate_ideas)
    _assert("taken_examples" in sig.parameters, "параметр taken_examples есть", errors)
    _assert(sig.parameters["taken_examples"].default is None, "опционален (None)", errors)

    fake = _FakeClaude()
    ideas = ig.generate_ideas(
        fake, "maksim", exclude_titles=["Старая тема"], n=5,
        taken_examples=[{"title": "Найм сильных", "rubric": "TG-пост"}],
    )
    _assert(len(ideas) == 5, f"5 идей вернулось, got {len(ideas)}", errors)
    msg = fake.last_user_msg or ""
    _assert("Найм сильных" in msg, "взятая идея в промпте", errors)
    _assert(("ЗАШЛО" in msg or "ВЗЯЛ" in msg), "блок вкуса в промпте", errors)
    _assert("Старая тема" in msg, "exclude-дедуп никуда не делся", errors)
    _assert(msg.index("УЖЕ БЫЛО") < msg.index("Найм сильных"),
            "порядок: сначала дедуп, потом вкус", errors)

    fake2 = _FakeClaude()
    ig.generate_ideas(fake2, "maksim", n=5)
    _assert("ЗАШЛО" not in (fake2.last_user_msg or ""),
            "без примеров блок не подмешивается", errors)

    print("\n[bot.py — передаёт взятые карточки]")
    bot_src = Path(__file__).parent.parent.joinpath("bot.py").read_text(
        encoding="utf-8", errors="replace")
    _assert("taken_examples" in bot_src, "bot.py передаёт taken_examples", errors)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
