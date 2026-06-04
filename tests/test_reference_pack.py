"""TDD: reference_pack.md — curated выжимка из HF-скилла, которую оркестратор
передаёт Клоду вместо «прочитай 19 файлов».

Контекст (синтез DR + ChatGPT, 1 июня): главная боль — монотонность. Claude
не читает глубокую базу скилла (progressive disclosure). Решение — оркестратор
САМ собирает компактный reference_pack с визуальным вокабуляром + анти-паттернами
и кладёт в проект, чтобы Claude гарантированно его видел.

Содержимое pack верифицировано против РЕАЛЬНЫХ файлов скилла (прочитаны с сервера
1 июня): data-in-motion.md, house-style.md, motion-principles.md, techniques.md,
visual-styles.md.

Тест проверяет ПОЛНОТУ pack (структуру и связь с валидатором), не выдумывая
содержание. Run: python tests/test_reference_pack.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
sys.path.insert(0, str(Path(__file__).parent.parent))

import storyboard_validator as SV  # noqa: E402

PACK = Path(__file__).parent.parent / "hyperframes_assets" / "reference_pack.md"


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_pack_exists(errors):
    print("\n-- pack существует и непустой --")
    _assert(PACK.exists(), f"файл {PACK.name} существует", errors)
    if PACK.exists():
        _assert(len(PACK.read_text(encoding="utf-8")) > 1500, "pack содержательный (>1500 симв)", errors)


def test_required_sections(errors):
    print("\n-- обязательные секции --")
    if not PACK.exists():
        return
    txt = PACK.read_text(encoding="utf-8").upper()
    for sec in ["HARD", "LAYOUT", "TYPOGRAPH", "MOTION", "ARCHETYPE", "VISUAL STYLE", "ANTI", "DATA"]:
        _assert(sec in txt, f"есть секция про {sec.lower()}", errors)


def test_all_business_archetypes_present(errors):
    print("\n-- все 12 business-архетипов из валидатора упомянуты --")
    if not PACK.exists():
        return
    txt = PACK.read_text(encoding="utf-8")
    for arch in sorted(SV.BUSINESS_ARCHETYPES):
        _assert(arch in txt, f"архетип '{arch}' в pack", errors)


def test_all_visual_styles_present(errors):
    print("\n-- все 8 visual-styles упомянуты --")
    if not PACK.exists():
        return
    txt = PACK.read_text(encoding="utf-8")
    for st in sorted(SV.VISUAL_STYLES):
        _assert(st in txt, f"стиль '{st}' в pack", errors)


def test_hard_rules_present(errors):
    print("\n-- жёсткие GSAP/HTML-правила (детерминизм) --")
    if not PACK.exists():
        return
    txt = PACK.read_text(encoding="utf-8")
    for rule in ["Math.random", "Date.now", "repeat: -1", "__timelines"]:
        _assert(rule in txt, f"правило '{rule}' указано", errors)


def test_anti_patterns_present(errors):
    print("\n-- анти-паттерны (из data-in-motion + house-style) --")
    if not PACK.exists():
        return
    low = PACK.read_text(encoding="utf-8").lower()
    # ключевые запреты, которые прямо бьют в нашу монотонность
    _assert("pie" in low, "запрет pie charts", errors)
    _assert("d3" in low or "chart.js" in low, "запрет chart-библиотек (D3/Chart.js)", errors)
    _assert("identical card" in low or "одинаков" in low, "запрет identical card grids (наша монотонность)", errors)


def test_safe_area_geometry(errors):
    print("\n-- наша split-layout геометрия (НЕ generic safe-area) --")
    if not PACK.exists():
        return
    txt = PACK.read_text(encoding="utf-8")
    _assert("1040" in txt and "480" in txt and "1440" in txt, "x∈[40,1040], y∈[480,1440] указаны", errors)


def test_motion_variety_rules(errors):
    print("\n-- motion-вокабуляр против монотонности --")
    if not PACK.exists():
        return
    low = PACK.read_text(encoding="utf-8").lower()
    _assert("build" in low and "breathe" in low and "resolve" in low, "build/breathe/resolve фазы", errors)
    _assert("ease" in low, "правило про разные ease", errors)


def main():
    print("=" * 60)
    print("test_reference_pack")
    print("=" * 60)
    errors = []
    test_pack_exists(errors)
    test_required_sections(errors)
    test_all_business_archetypes_present(errors)
    test_all_visual_styles_present(errors)
    test_hard_rules_present(errors)
    test_anti_patterns_present(errors)
    test_safe_area_geometry(errors)
    test_motion_variety_rules(errors)
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
