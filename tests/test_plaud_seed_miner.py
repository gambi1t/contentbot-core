"""Тест майнера seeds из Plaud-планёрок (11 июня).

Требование Артёма (явное): личная информация НЕ должна утечь — никаких имён,
цифр/денег, контрагентов, конкретных постановок задач. Из транскриптов
достаём только ОБЕЗЛИЧЕННЫЕ предпринимательские темы-углы уровня существующих
seeds («Делегирование задачи vs делегирование решения»).

Защита (3 слоя): extraction-промпт с запретами → judge-промпт (вторая LLM-
проверка) → механический фильтр (_privacy_filter: цифры, деньги, %, имена-
паттерны, длина). Плюс дедуп против существующих seeds (Jaccard из
idea_generator — переиспользование).

Запуск: python tests/test_plaud_seed_miner.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import plaud_seed_miner as psm  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[_privacy_filter — механический слой]")
    ok, rejected = psm._privacy_filter([
        "Делегирование смены: кто закрывает объект, когда все заняты",
        "Платим 80 тысяч, а кандидат хочет 120",          # деньги/цифры
        "Конфликт Сергея и администратора из-за графика",  # имя
        "Поставить задачу закупить дрова к пятнице",       # постановка задачи
        "Маржа упала на 15%",                              # цифры/процент
        "",                                                # пустое
        "Оч",                                              # слишком короткое
        "Почему сильный администратор уходит даже при хорошей зарплате",
    ])
    _assert("Делегирование смены: кто закрывает объект, когда все заняты" in ok,
            "чистая тема проходит", errors)
    _assert("Почему сильный администратор уходит даже при хорошей зарплате" in ok,
            "вторая чистая тема проходит", errors)
    _assert(not any("80" in s or "15%" in s for s in ok),
            "строки с цифрами/деньгами отрезаны", errors)
    _assert(not any("Сергея" in s for s in ok), "строка с именем отрезана", errors)
    _assert(not any("оставить задачу" in s.lower() or "поставить задачу" in s.lower()
                    for s in ok),
            "постановка задачи отрезана", errors)
    _assert(len(rejected) >= 4, f"отклонённые логируются, got {len(rejected)}", errors)

    print("\n[длина и формат]")
    long = "о" * 120
    ok2, _ = psm._privacy_filter([long])
    _assert(ok2 == [], "длиннее 100 символов — отрезано", errors)

    print("\n[_extraction_prompt — запреты прописаны]")
    p = psm._extraction_prompt("текст планёрки")
    low = p.lower()
    for marker in ("имён", "цифр", "задач"):
        _assert(marker in low or marker.replace("ё", "е") in low,
                f"запрет «{marker}» в extraction-промпте", errors)
    _assert("обезлич" in low, "требование обезличивания", errors)
    _assert("json" in low, "формат ответа JSON", errors)

    print("\n[_judge_prompt — вторая проверка приватности]")
    j = psm._judge_prompt(["тема 1", "тема 2"])
    jl = j.lower()
    _assert("персональн" in jl or "конфиденциальн" in jl or "личн" in jl,
            "judge проверяет персональное", errors)
    _assert("тема 1" in j and "тема 2" in j, "кандидаты в judge-промпте", errors)

    print("\n[_dedup_new_seeds — против существующих, через Jaccard]")
    existing = ["Делегирование задачи vs делегирование решения",
                "Найм сильных — почему лучшие приходят не через HH"]
    fresh = psm._dedup_new_seeds(
        ["Делегирование решения vs делегирование задачи",   # перестановка = дубль
         "Сезонные качели выручки: как пережить мёртвый сезон"],
        existing,
    )
    _assert(fresh == ["Сезонные качели выручки: как пережить мёртвый сезон"],
            f"дубль по Jaccard отрезан, got {fresh}", errors)

    print("\n[_parse_seed_json — разбор ответа LLM]")
    _assert(psm._parse_seed_json('["а", "б"]') == ["а", "б"], "чистый JSON", errors)
    _assert(psm._parse_seed_json('Вот:\n["в"]\nготово') == ["в"], "JSON в прозе", errors)
    _assert(psm._parse_seed_json("мусор") == [], "мусор → пустой список", errors)

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
