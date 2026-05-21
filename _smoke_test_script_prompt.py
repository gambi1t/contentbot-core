"""Smoke-тест нового script_prompt_maksim.txt — pipeline #3 (идея → сценарий).

Прогоняет Opus с новым промптом на реальной идее Артёма (та, что в боте
дала слабую воду). Проверяет глазами: сохранена ли конкретика, бьёт ли хук,
нет ли лишнего глэмпинга и жаргона.

Запуск:  python _smoke_test_script_prompt.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).parent

# Идея Артёма — дословно из бота (та, что дала слабый сценарий).
IDEA = (
    "В последний месяц я устанавливаю ассистента, который помогает мне "
    "планировать моё время, а также он интегрирован с моим устройством Ploud. "
    "Теперь все встречи, которые я провожу, — по итогам встречи автоматически "
    "приходят задачи на моих сотрудников, если я провожу планёрки. Также была "
    "интеграция с Битрикс, и теперь по итогам дня мне приходит отчёт, как мои "
    "сотрудники выполнили задачи. Я могу поставить задачу прямо из окна, из "
    "чата своего ассистента."
)


def main() -> int:
    load_dotenv(override=True)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("FAIL: нет ANTHROPIC_API_KEY")
        return 1

    prompt = (BASE / "script_prompt_maksim.txt").read_text(encoding="utf-8")

    import anthropic
    claude = anthropic.Anthropic(api_key=api_key)
    resp = claude.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": IDEA}],
    )
    script = resp.content[0].text.strip()
    if script.upper().startswith("СЦЕНАРИЙ"):
        script = script.split("\n", 1)[-1].strip()

    print("=" * 64)
    print("ИДЕЯ (вход):")
    print(IDEA)
    print("=" * 64)
    print("СЦЕНАРИЙ (новый промпт):")
    print(script)
    print("=" * 64)
    print(f"длина: {len(script)} символов")

    # Эвристические проверки
    low = script.lower()
    jargon = [w for w in ("нейросеть", "ии", "llm", "gpt", "ai-агент",
                           "промпт", "искусственный интеллект")
              if w in low]
    has_concrete = any(w in low for w in ("планёрк", "задач", "отчёт", "ассистент"))
    glamping_forced = "глэмпинг" in low or "картинг" in low
    print()
    print(f"{'OK' if not jargon else 'WARN'}: жаргон — {jargon or 'нет'}")
    print(f"{'OK' if has_concrete else 'WARN'}: конкретика идеи (планёрка/задачи/отчёт/ассистент) — "
          f"{'сохранена' if has_concrete else 'ПОТЕРЯНА'}")
    print(f"{'OK' if not glamping_forced else 'WARN'}: глэмпинг/картинг — "
          f"{'приплетён (проверить, уместно ли)' if glamping_forced else 'не приплетён'}")
    print(f"{'OK' if len(script) <= 600 else 'WARN'}: длина — "
          f"{len(script)} (лимит 600)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
