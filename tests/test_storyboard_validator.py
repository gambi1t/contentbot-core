"""TDD: storyboard_validator — машинный diversity-гейт для HyperFrames-генерации.

Контекст (1 июня, синтез Deep Research + ChatGPT CTO-ревью):
  Главная боль — монотонность (3/6 сцен один bar-chart). Решение по обоим
  источникам: НЕ «длиннее промпт», а machine-validated storyboard.json как
  ГЕЙТ перед генерацией HTML. reference-load.md (DR) — слабый audit; настоящий
  гейт — этот валидатор (ChatGPT).

Контракт storyboard:
  - ровно 6 сцен, id = scene_01..scene_06 по порядку
  - поля: id, script_beat, business_archetype, hf_technique, visual_style,
    motion_family, density, scale_profile, primary_text, reason
  - enum-поля в допустимых значениях

Diversity-правила (поверх схемы):
  - соседние сцены НЕ повторяют business_archetype
  - ≥5 уникальных business_archetype из 6
  - ≥4 уникальных motion_family
  - ≥2 density, ≥2 scale_profile
  - НЕ 3 подряд одинаковой density / scale_profile
  - data-chart-архетипы (cashflow_timeline/table_snapshot/calendar_grid) ≤2 суммарно
  - scene_06 = final_cta (финал — призыв, не данные)
  - primary_text 3..80 симв, script_beat ≥20 симв

Run: python tests/test_storyboard_validator.py
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
sys.path.insert(0, str(Path(__file__).parent.parent))

import storyboard_validator as SV  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


# ── Эталонный валидный storyboard (6 разных архетипов, разнообразие) ──────
def _golden() -> dict:
    scenes = [
        ("scene_01", "hero_number", "snap_reveal", "swiss_pulse", "hero", "balanced",
         "Прибыль приходит сезоном, расходы — каждый месяц"),
        ("scene_02", "cashflow_timeline", "path_draw", "data_drift", "medium", "dense",
         "Выручка волной, расходы ровной линией"),
        ("scene_03", "reserve_gauge", "counter_build", "shadow_cut", "hero", "sparse",
         "Резерв считаю от месяцев простоя"),
        ("scene_04", "before_after_cards", "card_flip", "velvet_standard", "medium", "balanced",
         "Было: процент с продаж. Стало: месяцы простоя"),
        ("scene_05", "checklist", "vertical_stack", "soft_signal", "compact", "balanced",
         "Меняешь логику, решаешь из устойчивости"),
        ("scene_06", "final_cta", "kinetic_type", "maximalist_type", "hero", "sparse",
         "Юмсунов про реальный бизнес"),
    ]
    return {
        "version": "1.0",
        "scenes": [
            {
                "id": sid, "business_archetype": arch, "hf_technique": "svg_path_drawing",
                "visual_style": style, "motion_family": motion, "scale_profile": scale,
                "density": dens, "primary_text": text,
                "script_beat": "Фрагмент сценария про финансовый резерв в сезонном бизнесе.",
                "reason": "Этот архетип лучше всего иллюстрирует данный момент сценария.",
            }
            for (sid, arch, motion, style, scale, dens, text) in scenes
        ],
    }


def _golden_n(n: int) -> dict:
    """Валидный storyboard на N сцен (для A — масштаб под длину). Разнообразие
    архетипов/motion/density/scale соблюдено, последняя сцена = final_cta."""
    arch_pool = ["hero_number", "cashflow_timeline", "reserve_gauge", "before_after_cards",
                 "checklist", "risk_matrix", "table_snapshot", "formula_card", "stack_layers", "path_map"]
    motions = sorted(SV.MOTION_FAMILIES)
    styles = sorted(SV.VISUAL_STYLES)
    dens = ["sparse", "balanced", "dense"]
    scales = ["hero", "medium", "compact"]
    scenes = []
    for i in range(n - 1):
        scenes.append({
            "id": f"scene_{i+1:02d}", "business_archetype": arch_pool[i % len(arch_pool)],
            "hf_technique": "svg_path_drawing", "visual_style": styles[i % len(styles)],
            "motion_family": motions[i % len(motions)], "density": dens[i % 3],
            "scale_profile": scales[i % 3], "primary_text": f"Текст сцены {i+1}",
            "script_beat": "Фрагмент сценария достаточной длины для валидатора здесь.",
            "reason": "Этот архетип лучше всего иллюстрирует данный момент сценария.",
        })
    scenes.append({
        "id": f"scene_{n:02d}", "business_archetype": "final_cta",
        "hf_technique": "kinetic_typography", "visual_style": "maximalist_type",
        "motion_family": "kinetic_type", "density": "sparse", "scale_profile": "hero",
        "primary_text": "Подпишись на канал",
        "script_beat": "Финальный призыв подписаться на канал автора здесь.",
        "reason": "Финальная сцена — призыв к действию, итог ролика.",
    })
    return {"version": "1.0", "scenes": scenes}


def test_parameterized_scene_count(errors):
    print("\n-- A: validate_storyboard(n_scenes=8) — 8 сцен ок, 6 при n=8 fail --")
    ok8, e8 = SV.validate_storyboard(_golden_n(8), n_scenes=8)
    _assert(ok8, f"8 валидных сцен проходят при n_scenes=8 (errs={e8})", errors)
    ok6, e6 = SV.validate_storyboard(_golden(), n_scenes=8)
    _assert(not ok6 and any("8" in e for e in e6), f"6 сцен при n_scenes=8 → fail (errs={e6})", errors)
    _assert(SV.validate_storyboard(_golden_n(5), n_scenes=5)[0], "5 сцен ок при n_scenes=5", errors)


def test_default_still_6(errors):
    print("\n-- A: дефолт n_scenes=6 (backward-compat) --")
    ok, e = SV.validate_storyboard(_golden())  # без n_scenes
    _assert(ok, f"golden(6) проходит по дефолту (errs={e})", errors)


def test_diversity_scales_with_count(errors):
    print("\n-- A: для 8 сцен порог уникальных архетипов выше (мало → fail) --")
    d = _golden_n(8)
    # 4 уникальных архетипа на 8 сцен (не-соседние повторы) → ниже порога для n=8
    for idx, a in [(0, "hero_number"), (2, "hero_number"), (4, "hero_number"),
                   (1, "checklist"), (3, "checklist"), (5, "checklist"), (6, "stack_layers")]:
        d["scenes"][idx]["business_archetype"] = a
    ok, e = SV.validate_storyboard(d, n_scenes=8)
    _assert(not ok and any("уникальн" in x.lower() or "архетип" in x.lower() for x in e),
            f"4 уникальных на 8 сцен → fail (errs={e})", errors)


def test_golden_valid(errors):
    print("\n-- эталонный storyboard валиден --")
    ok, errs = SV.validate_storyboard(_golden())
    _assert(ok, f"golden проходит (errs={errs})", errors)


def test_wrong_scene_count(errors):
    print("\n-- не 6 сцен → fail --")
    d = _golden(); d["scenes"] = d["scenes"][:5]
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("6" in e for e in errs), f"ловит !=6 сцен (errs={errs})", errors)


def test_bad_enum(errors):
    print("\n-- неизвестный business_archetype → fail --")
    d = _golden(); d["scenes"][0]["business_archetype"] = "pie_chart_party"
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("archetype" in e.lower() for e in errs), f"ловит чужой enum (errs={errs})", errors)


def test_adjacent_repeat(errors):
    print("\n-- соседние сцены с одинаковым архетипом → fail --")
    d = _golden()
    d["scenes"][1]["business_archetype"] = d["scenes"][0]["business_archetype"]  # 01==02
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("сосед" in e.lower() or "adjacent" in e.lower() for e in errs),
            f"ловит соседний повтор (errs={errs})", errors)


def test_too_few_unique_archetypes(errors):
    print("\n-- <5 уникальных архетипов (монотонность) → fail --")
    d = _golden()
    # делаем 3 разных архетипа размазав по не-соседним: 01,03,05 = hero_number,
    # 02,04 = checklist, 06 = final_cta → 3 уникальных, соседние различны
    d["scenes"][0]["business_archetype"] = "hero_number"
    d["scenes"][2]["business_archetype"] = "hero_number"
    d["scenes"][4]["business_archetype"] = "hero_number"
    d["scenes"][1]["business_archetype"] = "checklist"
    d["scenes"][3]["business_archetype"] = "checklist"
    d["scenes"][5]["business_archetype"] = "final_cta"
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("уникальн" in e.lower() or "архетип" in e.lower() for e in errs),
            f"ловит <5 уникальных (errs={errs})", errors)


def test_too_many_charts(errors):
    print("\n-- >2 data-chart архетипов → fail --")
    d = _golden()
    d["scenes"][0]["business_archetype"] = "cashflow_timeline"
    d["scenes"][2]["business_archetype"] = "table_snapshot"
    d["scenes"][4]["business_archetype"] = "calendar_grid"  # 3 чарта
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("чарт" in e.lower() or "chart" in e.lower() for e in errs),
            f"ловит >2 чартов (errs={errs})", errors)


def test_final_not_cta(errors):
    print("\n-- scene_06 не final_cta → fail --")
    d = _golden(); d["scenes"][5]["business_archetype"] = "checklist"
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("cta" in e.lower() or "финал" in e.lower() for e in errs),
            f"ловит не-CTA финал (errs={errs})", errors)


def test_too_few_motion(errors):
    print("\n-- <4 уникальных motion_family → fail --")
    d = _golden()
    for i in range(5):
        d["scenes"][i]["motion_family"] = "snap_reveal"  # 5 одинаковых + 1 = 2 уникальных
    # но соседние motion могут повторяться — это правило только про archetype.
    # чиним archetype-разнообразие нетронутым, ломаем только motion.
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("motion" in e.lower() for e in errs),
            f"ловит <4 motion (errs={errs})", errors)


def test_three_same_density_in_row(errors):
    print("\n-- 3 подряд одинаковой density → fail --")
    d = _golden()
    d["scenes"][0]["density"] = "balanced"
    d["scenes"][1]["density"] = "balanced"
    d["scenes"][2]["density"] = "balanced"  # 3 подряд
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("подряд" in e.lower() or "density" in e.lower() for e in errs),
            f"ловит 3 подряд density (errs={errs})", errors)


def test_primary_text_too_long(errors):
    print("\n-- primary_text > 80 символов → fail --")
    d = _golden(); d["scenes"][0]["primary_text"] = "С" * 90
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("primary_text" in e.lower() or "80" in e for e in errs),
            f"ловит длинный primary_text (errs={errs})", errors)


def test_missing_field(errors):
    print("\n-- отсутствует обязательное поле → fail --")
    d = _golden(); del d["scenes"][0]["reason"]
    ok, errs = SV.validate_storyboard(d)
    _assert(not ok and any("reason" in e.lower() for e in errs),
            f"ловит отсутствие поля (errs={errs})", errors)


def test_errors_are_human_readable(errors):
    print("\n-- ошибки человекочитаемы (пойдут Клоду в fix-round) --")
    d = _golden(); d["scenes"][1]["business_archetype"] = d["scenes"][0]["business_archetype"]
    ok, errs = SV.validate_storyboard(d)
    _assert(errs and all(isinstance(e, str) and len(e) > 10 for e in errs),
            "ошибки — непустые осмысленные строки", errors)
    _assert(any("scene_0" in e for e in errs), "ошибка ссылается на конкретную сцену", errors)


def main():
    print("=" * 60)
    print("test_storyboard_validator")
    print("=" * 60)
    errors = []
    _assert(hasattr(SV, "validate_storyboard"), "validate_storyboard существует", errors)
    if not hasattr(SV, "validate_storyboard"):
        print("\nFAIL: модуль не готов")
        return 1
    test_parameterized_scene_count(errors)
    test_default_still_6(errors)
    test_diversity_scales_with_count(errors)
    test_golden_valid(errors)
    test_wrong_scene_count(errors)
    test_bad_enum(errors)
    test_adjacent_repeat(errors)
    test_too_few_unique_archetypes(errors)
    test_too_many_charts(errors)
    test_final_not_cta(errors)
    test_too_few_motion(errors)
    test_three_same_density_in_row(errors)
    test_primary_text_too_long(errors)
    test_missing_field(errors)
    test_errors_are_human_readable(errors)
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
