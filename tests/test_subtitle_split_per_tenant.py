"""Per-tenant позиция субтитров в split-сегменте.

Контекст (Артём 23.06): в аватарном комбайне (split 50/50 → фуллскрин → split)
субтитр в split-сегменте РАНЬШЕ вставал на стык половин (его контент-бот: MarginV
900 = середина кадра). 10 июня для Максима стык опустили в 150 (текст ложился на
лицо при поднятом аватаре — close-up селфи). panferov переехал на общее ядро и
унаследовал Максимову настройку 150 → потерял стык.

Решение (per-tenant, подтверждено Артёмом): panferov → стык (PANFEROV_SPLIT_MARGIN_V),
maksim/default → 150 (фидбэк Максима не ломаем). Механизм per-tenant уже есть в
subtitle_burner (бренд-лексикон через tenant.active_tenant_id()).

Запуск: python tests/test_subtitle_split_per_tenant.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import tenant  # noqa: E402
import subtitle_burner as sb  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _with_tenant(tid):
    tenant.active_tenant_id = lambda: tid


_PLAN = [
    {"start": 0.0, "end": 2.5, "layout": "avatar_full"},
    {"start": 2.5, "end": 8.0, "layout": "split"},
    {"start": 8.0, "end": 12.0, "layout": "broll_full"},
]


def test_split_margin_v_per_tenant(errors):
    print("\n-- split_margin_v(): panferov → стык, maksim/default → 150 --")
    _with_tenant("panferov")
    _assert(sb.split_margin_v() == sb.PANFEROV_SPLIT_MARGIN_V, f"panferov → {sb.PANFEROV_SPLIT_MARGIN_V} (стык)", errors)
    _assert(sb.split_margin_v() > sb.SPLIT_MARGIN_V, "panferov стык ВЫШЕ Максимова низа", errors)
    _with_tenant("maksim")
    _assert(sb.split_margin_v() == sb.SPLIT_MARGIN_V, f"maksim → {sb.SPLIT_MARGIN_V} (не трогаем)", errors)
    _with_tenant("default")
    _assert(sb.split_margin_v() == sb.SPLIT_MARGIN_V, "default → 150 (backward-compat)", errors)


def test_margin_for_word_split_per_tenant(errors):
    print("\n-- _margin_for_word: split-слово per-tenant, остальные одинаково --")
    _with_tenant("panferov")
    _assert(sb._margin_for_word(5.0, _PLAN) == sb.PANFEROV_SPLIT_MARGIN_V, "panferov split-слово → стык", errors)
    _assert(sb._margin_for_word(1.0, _PLAN) == sb.DEFAULT_MARGIN_V, "panferov avatar_full → 300 (как у всех)", errors)
    _assert(sb._margin_for_word(9.0, _PLAN) == sb.DEFAULT_MARGIN_V, "panferov broll_full → 300", errors)
    _with_tenant("maksim")
    _assert(sb._margin_for_word(5.0, _PLAN) == sb.SPLIT_MARGIN_V, "maksim split-слово → 150 (без изменений)", errors)


def test_generate_ass_has_junction_for_panferov(errors):
    print("\n-- generate_ass: у panferov стык в ASS есть, у maksim — нет --")
    words = [{"word": "смотри", "start": 5.0, "end": 5.4}]  # split-слово
    _with_tenant("panferov")
    out_p = Path(tempfile.mkdtemp()) / "p.ass"
    sb.generate_ass(words, out_p, montage_plan=_PLAN)
    tp = out_p.read_text(encoding="utf-8")
    _assert(f",{sb.PANFEROV_SPLIT_MARGIN_V}," in tp, f"panferov ASS содержит стык {sb.PANFEROV_SPLIT_MARGIN_V}", errors)
    _with_tenant("maksim")
    out_m = Path(tempfile.mkdtemp()) / "m.ass"
    sb.generate_ass(words, out_m, montage_plan=_PLAN)
    tm = out_m.read_text(encoding="utf-8")
    _assert(f",{sb.PANFEROV_SPLIT_MARGIN_V}," not in tm, "maksim ASS НЕ содержит стык (остаётся 150)", errors)
    _assert(f",{sb.SPLIT_MARGIN_V}," in tm, "maksim ASS содержит 150", errors)


def test_no_plan_still_zero(errors):
    print("\n-- без montage_plan → 0 (стиль по умолчанию), не падаем по тенанту --")
    _with_tenant("panferov")
    _assert(sb._margin_for_word(5.0, None) == 0, "panferov без плана → 0", errors)


def main():
    print("=" * 60 + "\nPer-tenant split subtitle margin\n" + "=" * 60)
    errors = []
    _orig = tenant.active_tenant_id
    try:
        for fn in (test_split_margin_v_per_tenant, test_margin_for_word_split_per_tenant,
                   test_generate_ass_has_junction_for_panferov, test_no_plan_still_zero):
            fn(errors)
    finally:
        tenant.active_tenant_id = _orig
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all per-tenant split-margin tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
