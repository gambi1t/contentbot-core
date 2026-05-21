"""Smoke-тест cover-генерации pipeline #3 — что предложит текущий промпт.

Воспроизводит ТОЧНО вызов из bot.py:15844-15855: system =
cover_prompt_maksim.txt, тот же user-message, model = COVER_MODEL.
Затем прогоняет парсер кода (split по строкам + фильтр длины) — показывает,
что реально станет кнопками-вариантами обложки.

Запуск:  python _smoke_test_cover_prompt.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).parent

# Наш сценарий (выход нового script_prompt_maksim.txt на идее Артёма).
SCRIPT = (
    "Я перестал ставить задачи сотрудникам руками.\n\n"
    "Месяц назад поставил себе ассистента и связал его с диктофоном, который "
    "ношу с собой. Теперь схема такая: я провёл планёрку — задачи сами улетели "
    "в Битрикс на конкретных людей. Я ничего не записываю, ничего не дублирую.\n\n"
    "Вечером прилетает отчёт: что сделано, что висит, кто тормозит. Нужно "
    "поставить задачу — пишу одну строку в чат ассистенту, она уходит в работу.\n\n"
    "Раньше я был диспетчером собственной компании. Теперь у меня освободилась "
    "голова под то, ради чего я её и заводил.\n\n"
    "Как я это собрал и какие сервисы связал — разбираю по шагам в "
    "Telegram-канале «Юмсунов | Про реальный бизнес»."
)

# user-message — дословно из bot.py:15850
USER_MSG = (
    f"Сценарий:\n{SCRIPT}\n\n"
    "Придумай 5 вирусных текстов для обложки. Найди в сценарии самый "
    "ШОКИРУЮЩИЙ факт или цифру — и построй обложку вокруг него. Каждый текст "
    "должен ИНТРИГОВАТЬ. Каждый на новой строке, только текст, без нумерации."
)


def main() -> int:
    load_dotenv(override=True)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("FAIL: нет ANTHROPIC_API_KEY")
        return 1

    prompt = (BASE / "cover_prompt_maksim.txt").read_text(encoding="utf-8")

    import anthropic
    claude = anthropic.Anthropic(api_key=api_key)
    resp = claude.messages.create(
        model="claude-opus-4-7",
        max_tokens=300,
        system=prompt,
        messages=[{"role": "user", "content": USER_MSG}],
    )
    raw = resp.content[0].text.strip()

    print("=" * 64)
    print("СЫРОЙ ОТВЕТ Opus (system = cover_prompt_maksim.txt):")
    print("=" * 64)
    print(raw)
    print("=" * 64)

    # Парсер из bot.py:15854-15855
    options = [
        line.strip().strip('"').strip("«»").strip("-").strip()
        for line in raw.split("\n") if line.strip()
    ]
    options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]

    print("ЧТО СТАНЕТ КНОПКАМИ-ВАРИАНТАМИ (после парсера кода):")
    print("=" * 64)
    if not options:
        print("(пусто — парсер ничего не пропустил)")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    print("=" * 64)
    print(f"вариантов после парсера: {len(options)} (ожидалось 5)")
    has_lime = "<lime>" in raw
    print(f"lime-теги в ответе: {'ДА — попадут в текст кнопок' if has_lime else 'нет'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
