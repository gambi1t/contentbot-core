"""TDD: для модели ElevenLabs v3 короткий сценарий озвучивается ОДНИМ блоком
(не делится на части), иначе на стыке частей плывёт интонация.

Правило (`bot._voice_target_parts`):
  - model startswith "eleven_v3" И len(text) <= cap (1000) → 1 (единый блок)
  - иначе → None (авто-разбивка, старое поведение)

Длинные сценарии (>cap) у v3 всё равно делятся — защита от обрезки ElevenLabs.
v2 не трогаем — авто-разбивка как раньше.

Run: python tests/test_voice_v3_single_block.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Dummy env, чтобы `import bot` не упал на отсутствии ключей.
os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


# ~35s сценарий Максима (~520 симв) — реальный кейс из теста Артёма.
SHORT_V3 = (
    "Прибыль приходит сезоном, а аренда и зарплаты каждый месяц. Резерв в "
    "процентах тут не работает. В сезонном бизнесе расходы идут ровно весь год: "
    "аренда, зарплаты, обслуживание. А выручка окном. Резерв — это статья "
    "расходов. Считаю от месяцев простоя. Сколько нужно, чтобы спокойно пройти "
    "низкий сезон. Меняешь логику — и решаешь из устойчивости, а не из страха."
)
# Длинный (>1000) — должен делиться даже на v3.
LONG = SHORT_V3 * 3


def test_target_parts(errors: list[str]) -> None:
    print("\n-- _voice_target_parts --")
    f = getattr(bot, "_voice_target_parts", None)
    _assert(callable(f), "_voice_target_parts exists", errors)
    if not f:
        return
    _assert(f("eleven_v3", SHORT_V3) == 1, "v3 + короткий → 1 блок", errors)
    _assert(f("eleven_v3_alpha", SHORT_V3) == 1, "v3-вариант + короткий → 1 блок", errors)
    _assert(f("eleven_v3", LONG) is None, "v3 + длинный (>1000) → авто (None)", errors)
    _assert(f("eleven_multilingual_v2", SHORT_V3) is None, "v2 → авто (None)", errors)
    _assert(f("", SHORT_V3) is None, "пустая модель → авто (None)", errors)
    _assert(f(None, SHORT_V3) is None, "None модель → авто (None)", errors)


def test_split_single_block(errors: list[str]) -> None:
    print("\n-- split_script_to_parts(target_parts=1) → единый блок --")
    parts = bot.split_script_to_parts(SHORT_V3, target_parts=1)
    _assert(len(parts) == 1, f"один блок (got {len(parts)})", errors)
    if parts:
        # весь смысл сохранён — последняя фраза на месте
        _assert("из страха" in parts[0], "финальная фраза в блоке", errors)
        _assert(len(parts[0]) >= len(SHORT_V3) - 5, "длина не потерялась", errors)
    # контроль: дефолт (None) для этого текста делит на 2
    auto = bot.split_script_to_parts(SHORT_V3)
    _assert(len(auto) >= 2, f"дефолт делит на >=2 (got {len(auto)})", errors)


def main() -> int:
    print("=" * 60)
    print("test_voice_v3_single_block")
    print("=" * 60)
    errors: list[str] = []
    test_target_parts(errors)
    test_split_single_block(errors)
    print()
    if errors:
        print(f"FAIL: {len(errors)} assertion(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
