"""storyboard_validator.py — машинный diversity-гейт для HyperFrames-генерации.

Зачем (синтез Deep Research + ChatGPT CTO-ревью, 1 июня 2026): главная боль
автономной генерации B-roll — МОНОТОННОСТЬ (Claude лепит один bar-chart в
нескольких сценах). Оба источника сошлись: решение НЕ «длиннее промпт», а
machine-validated `storyboard.json` как ГЕЙТ перед генерацией HTML. Этот модуль
— тот самый гейт.

Поток: Claude (фаза 1) пишет storyboard.json → `validate_storyboard()` →
если ошибки, они идут обратно Клоду на исправление (человекочитаемые строки) →
только валидный storyboard допускает фазу 2 (генерацию HTML).

Архетипы — ДВА слоя:
  • business_archetype — ЧТО показываем (под бизнес-контент): hero_number,
    before_after_cards, cashflow_timeline, reserve_gauge, checklist,
    risk_matrix, table_snapshot, formula_card, stack_layers, calendar_grid,
    path_map, final_cta.
  • hf_technique — КАК реализуем (HyperFrames techniques.md): svg_path_drawing,
    kinetic_typography и т.д. (свободная строка — проверяется непустота).
"""
from __future__ import annotations

# ── Словари допустимых значений ──────────────────────────────────────────
BUSINESS_ARCHETYPES = {
    "hero_number", "before_after_cards", "cashflow_timeline", "reserve_gauge",
    "checklist", "risk_matrix", "table_snapshot", "formula_card",
    "stack_layers", "calendar_grid", "path_map", "final_cta",
}
# «графики данных» — их суммарно ≤2 на ролик (анти-монотонность чартов)
CHART_ARCHETYPES = {"cashflow_timeline", "table_snapshot", "calendar_grid"}

MOTION_FAMILIES = {
    "snap_reveal", "slow_breathe", "counter_build", "path_draw",
    "card_flip", "vertical_stack", "radial_pulse", "kinetic_type",
}
VISUAL_STYLES = {
    "swiss_pulse", "velvet_standard", "deconstructed", "maximalist_type",
    "data_drift", "soft_signal", "folk_frequency", "shadow_cut",
}
DENSITY = {"sparse", "balanced", "dense"}
SCALE_PROFILE = {"hero", "medium", "compact"}

N_SCENES = 6
_REQUIRED_FIELDS = [
    "id", "script_beat", "business_archetype", "hf_technique", "visual_style",
    "motion_family", "density", "scale_profile", "primary_text", "reason",
]
_ENUMS = {
    "business_archetype": BUSINESS_ARCHETYPES,
    "motion_family": MOTION_FAMILIES,
    "visual_style": VISUAL_STYLES,
    "density": DENSITY,
    "scale_profile": SCALE_PROFILE,
}

# Пороги разнообразия
MIN_UNIQUE_ARCHETYPES = 5
MIN_UNIQUE_MOTION = 4
MIN_UNIQUE_DENSITY = 2
MIN_UNIQUE_SCALE = 2
MAX_CHART_ARCHETYPES = 2
PRIMARY_TEXT_MIN, PRIMARY_TEXT_MAX = 3, 80
SCRIPT_BEAT_MIN = 20


def validate_storyboard(data: dict) -> tuple[bool, list[str]]:
    """Проверяет storyboard на схему + diversity-правила.

    Возвращает (ok, errors). errors — человекочитаемые строки (на русском),
    которые можно отдать Клоду на исправление в fix-round.
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["storyboard должен быть JSON-объектом"]

    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        return False, ["нет массива 'scenes'"]

    # ── 1. Кол-во сцен ───────────────────────────────────────────────────
    if len(scenes) != N_SCENES:
        errors.append(f"должно быть ровно {N_SCENES} сцен, а найдено {len(scenes)}")

    # ── 2. Поля + enum по каждой сцене ───────────────────────────────────
    for i, sc in enumerate(scenes):
        tag = sc.get("id", f"scene[{i}]") if isinstance(sc, dict) else f"scene[{i}]"
        if not isinstance(sc, dict):
            errors.append(f"{tag}: сцена должна быть объектом")
            continue
        # обязательные поля
        for f in _REQUIRED_FIELDS:
            v = sc.get(f)
            if v is None or (isinstance(v, str) and not v.strip()):
                errors.append(f"{tag}: отсутствует или пустое поле '{f}'")
        # ожидаемый id по порядку
        expected_id = f"scene_{i+1:02d}"
        if sc.get("id") and sc["id"] != expected_id:
            errors.append(f"{tag}: id должен быть '{expected_id}' (по порядку)")
        # enum-поля
        for field, allowed in _ENUMS.items():
            val = sc.get(field)
            if val is not None and val not in allowed:
                errors.append(
                    f"{tag}: недопустимый {field}='{val}'. "
                    f"Разрешено: {', '.join(sorted(allowed))}"
                )
        # длины текстов
        pt = sc.get("primary_text")
        if isinstance(pt, str) and pt.strip():
            if not (PRIMARY_TEXT_MIN <= len(pt) <= PRIMARY_TEXT_MAX):
                errors.append(
                    f"{tag}: primary_text должен быть {PRIMARY_TEXT_MIN}..{PRIMARY_TEXT_MAX} "
                    f"символов (сейчас {len(pt)})"
                )
        sb = sc.get("script_beat")
        if isinstance(sb, str) and sb.strip() and len(sb) < SCRIPT_BEAT_MIN:
            errors.append(f"{tag}: script_beat слишком короткий (<{SCRIPT_BEAT_MIN} симв)")

    # Если структура совсем битая (нет 6 валидных сцен) — diversity не считаем
    valid_scenes = [s for s in scenes if isinstance(s, dict)]
    if len(valid_scenes) != N_SCENES:
        return (len(errors) == 0), errors

    arts = [s.get("business_archetype") for s in valid_scenes]
    motions = [s.get("motion_family") for s in valid_scenes]
    densities = [s.get("density") for s in valid_scenes]
    scales = [s.get("scale_profile") for s in valid_scenes]

    # ── 3. Соседние не повторяют архетип ─────────────────────────────────
    for i in range(len(arts) - 1):
        if arts[i] is not None and arts[i] == arts[i + 1]:
            errors.append(
                f"scene_{i+1:02d} и scene_{i+2:02d}: соседние сцены не должны "
                f"повторять business_archetype ('{arts[i]}')"
            )

    # ── 4. Разнообразие архетипов / motion / density / scale ─────────────
    uniq_arts = len({a for a in arts if a})
    if uniq_arts < MIN_UNIQUE_ARCHETYPES:
        errors.append(
            f"мало разнообразия: {uniq_arts} уникальных business_archetype, "
            f"нужно ≥{MIN_UNIQUE_ARCHETYPES} из {N_SCENES} (монотонность)"
        )
    uniq_motion = len({m for m in motions if m})
    if uniq_motion < MIN_UNIQUE_MOTION:
        errors.append(
            f"мало разнообразия движения: {uniq_motion} уникальных motion_family, "
            f"нужно ≥{MIN_UNIQUE_MOTION}"
        )
    if len({d for d in densities if d}) < MIN_UNIQUE_DENSITY:
        errors.append(f"нужно ≥{MIN_UNIQUE_DENSITY} разных density на 6 сцен")
    if len({s for s in scales if s}) < MIN_UNIQUE_SCALE:
        errors.append(f"нужно ≥{MIN_UNIQUE_SCALE} разных scale_profile на 6 сцен")

    # ── 5. Чарты ≤2 (анти-монотонность графиков) ─────────────────────────
    n_charts = sum(1 for a in arts if a in CHART_ARCHETYPES)
    if n_charts > MAX_CHART_ARCHETYPES:
        errors.append(
            f"слишком много графиков-чартов: {n_charts} "
            f"({', '.join(sorted(CHART_ARCHETYPES))}), максимум {MAX_CHART_ARCHETYPES}"
        )

    # ── 6. Финал — CTA ───────────────────────────────────────────────────
    if arts[-1] != "final_cta":
        errors.append(
            f"scene_06: финальная сцена должна быть 'final_cta' (призыв/итог), "
            f"а не '{arts[-1]}'"
        )

    # ── 7. Не 3 подряд одинаковой density / scale (визуальный ритм) ───────
    for name, seq in (("density", densities), ("scale_profile", scales)):
        for i in range(len(seq) - 2):
            if seq[i] is not None and seq[i] == seq[i + 1] == seq[i + 2]:
                errors.append(
                    f"scene_{i+1:02d}..scene_{i+3:02d}: 3 сцены подряд с одинаковым "
                    f"{name}='{seq[i]}' — ломает визуальный ритм, чередуй"
                )

    return (len(errors) == 0), errors


def format_errors_for_claude(errors: list[str]) -> str:
    """Готовит фидбек для fix-round: список нарушений storyboard."""
    if not errors:
        return ""
    lines = ["STORYBOARD не прошёл валидацию. Исправь и перезапиши storyboard.json:"]
    for e in errors:
        lines.append(f"  • {e}")
    return "\n".join(lines)
