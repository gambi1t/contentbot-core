"""TDD: HyperFrames промпт должен содержать safe-area constraint и правило
анти-overlap (по образцу Remotion-промпта в auto_broll.py).

Контекст (Артём 1 июня, после первого эксперимента):
  • На hf_04.mp4 заголовок «СЧИТАЮ ОТ МЕСЯЦЕВ ПРОСТОЯ» перекрывает
    карточку «6 МЕС» — visual collision на минимум 2-3 сценах из 6.
  • Когда B-roll вставляется в split-layout (video_assembler.py HALF_H=960),
    1080×1920 сцена сжимается до 1080×960 — текст становится мелким,
    критические элементы могут не влезть.
  • В Remotion-промпте (auto_broll.py:91-92) уже есть: «центральная полоса
    1080×960 (band y∈[480,1440]), ничего не выносить за полосу». В
    HyperFrames-промпте только размытое «по центру кадра» — Claude
    интерпретирует как полный экран.

Контракт фикса:
  (1) промпт явно упоминает safe-area 1080×960
  (2) промпт упоминает координаты или формулировку про «вертикальный центр»
  (3) промпт содержит anti-overlap правило (отступы между блоками,
      запрет накладывать текст на карточки)
  (4) сохраняется обратная совместимость (всё ещё HyperFrames-промпт)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_safe_area_explicit(errors: list[str]) -> None:
    print("\n-- промпт явно объявляет safe-area 1080×960 --")
    p = hyperframes_broll._build_prompt("DUMMY SCRIPT")
    _assert("1080×960" in p or "1080x960" in p, "упомянут размер 1080×960", errors)
    _assert(
        "safe-area" in p.lower() or "безопасн" in p.lower() or "центральн" in p.lower(),
        "есть формулировка safe-area / безопасной / центральной зоны",
        errors,
    )


def test_safe_area_coordinates_mentioned(errors: list[str]) -> None:
    print("\n-- координаты safe-area (y от 480 до 1440) --")
    p = hyperframes_broll._build_prompt("DUMMY SCRIPT")
    has_y = re.search(r"y[∈:= ]+\[?480.*1440\]?", p) is not None
    has_alt = ("480" in p and "1440" in p)
    _assert(has_y or has_alt, "y-координаты 480 и 1440 упомянуты", errors)


def test_anti_overlap_rule(errors: list[str]) -> None:
    print("\n-- anti-overlap правило (отступы, запрет наложения) --")
    p = hyperframes_broll._build_prompt("DUMMY SCRIPT")
    has_margin = re.search(r"\d+\s*(px|пикс|пиксел)", p, re.IGNORECASE) is not None
    has_overlap_words = (
        "не наклад" in p.lower()
        or "не пересек" in p.lower()
        or "не перекры" in p.lower()
        or "overlap" in p.lower()
        or "пересечени" in p.lower()
    )
    _assert(has_margin, "упомянуты конкретные пиксели отступов", errors)
    _assert(has_overlap_words, "есть запрет на overlap/наложение", errors)


def test_split_layout_warning(errors: list[str]) -> None:
    print("\n-- предупреждение про split-layout (B-roll в половине экрана) --")
    p = hyperframes_broll._build_prompt("DUMMY SCRIPT")
    has_split_warning = (
        "split" in p.lower()
        or "половин" in p.lower()
        or "сжима" in p.lower()
        or "обрез" in p.lower()
    )
    _assert(
        has_split_warning,
        "промпт объясняет ПОЧЕМУ safe-area (split может обрезать)",
        errors,
    )


def test_still_hf_prompt(errors: list[str]) -> None:
    print("\n-- сохраняется обратная совместимость с HyperFrames --")
    p = hyperframes_broll._build_prompt("DUMMY SCRIPT")
    _assert("HyperFrames" in p or "scene_" in p, "это HyperFrames-промпт", errors)
    _assert("scene_01.html" in p, "упомянуты scene-файлы", errors)
    _assert("DUMMY SCRIPT" in p, "сценарий подставлен", errors)


# ── Промпт-фикс v2 (1 июня вечер): убираем мои абсолютные Y-координаты,
# требуем flex-column по SKILL.md ─────────────────────────────────────────

def test_no_hardcoded_y_coordinates(errors: list[str]) -> None:
    """Корень overlap (scene_01: «выручка по месяцам» налезла на «СЕЗОН»):
    мои подсказки вида «заголовок (y=520-680), карточка (y=720-1200)» в
    промпте толкали Claude к `position:absolute; top:Npx`, а SKILL.md ПРЯМО
    это запрещает. Claude считал высоту 104px-заголовка вслепую → пересечение.
    Промпт НЕ должен содержать рекомендуемых Y-координат для блоков.
    """
    print("\n-- НЕТ моих хардкод Y-координат «заголовок (y=...)/карточка (y=...)» --")
    p = hyperframes_broll._build_prompt("DUMMY SCRIPT")
    # Y-координаты блоков (любая из ошибочных подсказок старого промпта):
    bad_patterns = [
        r"заголовок\s*\(y=\d+",
        r"карточка\s*\(y=\d+",
        r"CTA\s*\(y=\d+",
        r"y=520-680", r"y=720-1200", r"y=1240-1400",
    ]
    found = [pat for pat in bad_patterns if re.search(pat, p, re.IGNORECASE)]
    _assert(
        not found,
        f"нет рекомендуемых Y-координат блоков (найдено: {found})",
        errors,
    )


def test_requires_flex_column_layout(errors: list[str]) -> None:
    """SKILL.md строка 73: «NEVER position:absolute; top:Npx на контент-
    контейнерах — используй flex-column + gap + padding». Промпт должен
    явно требовать этот паттерн, а не оставлять Claude свободу выбирать
    absolute.
    """
    print("\n-- требование flex-column / gap / padding (по SKILL.md) --")
    p = hyperframes_broll._build_prompt("DUMMY SCRIPT")
    p_low = p.lower()
    has_flex = "flex-column" in p_low or "flex-direction: column" in p_low or "display: flex" in p_low or "display:flex" in p_low
    has_gap = "gap:" in p_low or " gap " in p_low or "gap=" in p_low
    has_no_absolute = (
        "position:absolute" in p_low.replace(" ", "") and "не использ" in p_low
    ) or "не используй position:absolute" in p_low.replace(" ", "") or "no position:absolute" in p_low.replace(" ", "") or "запрещ" in p_low and "absolute" in p_low
    _assert(has_flex, "упомянут flex-column / display:flex", errors)
    _assert(has_gap, "упомянут CSS gap (вертикальный ритм через gap)", errors)
    _assert(has_no_absolute, "явный запрет position:absolute для контента", errors)


def test_horizontal_safe_area(errors: list[str]) -> None:
    """scene_05 «20%» вылез за правый край (1080+). Моя safe-area была ТОЛЬКО
    вертикальная. Промпт должен явно требовать удержания контента и по X
    (например, padding контейнера 40px → контент в [40, 1040])."""
    print("\n-- горизонтальное удержание контента (X safe-area) --")
    p = hyperframes_broll._build_prompt("DUMMY SCRIPT")
    has_x_rule = (
        re.search(r"x[∈:= ]+\[?40", p) is not None
        or "1040" in p
        or "по горизонтали" in p.lower()
        or "горизонтальн" in p.lower()
        or "ширина" in p.lower() and "40" in p
    )
    _assert(
        has_x_rule,
        "промпт требует удержания контента по X (padding/ширина/40-1040)",
        errors,
    )


def main() -> int:
    print("=" * 60)
    print("test_hyperframes_prompt_safe_area")
    print("=" * 60)
    errors: list[str] = []
    test_safe_area_explicit(errors)
    test_safe_area_coordinates_mentioned(errors)
    test_anti_overlap_rule(errors)
    test_split_layout_warning(errors)
    test_still_hf_prompt(errors)
    test_no_hardcoded_y_coordinates(errors)
    test_requires_flex_column_layout(errors)
    test_horizontal_safe_area(errors)
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
