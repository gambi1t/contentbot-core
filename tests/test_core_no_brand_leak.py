"""TDD-страж: ядро (tenant-нейтральные модули) НЕ хардкодит бренд-палитру.

Бренд-цвета живут ТОЛЬКО в style_contract.<tenant>.json и резолвятся per-tenant
(style_contract.py: tenant.active_tenant_id() -> inline_for_prompt). Хардкод
значения палитры одного тенанта в коде ядра = протечка бренда (классика: оранжевый
Максима #FF5722, инжектируемый в промпт panferov «Илон», бело-синий). Этот тест —
постоянный регресс-замок к такой протечке. Модель: docs/PARALLEL_SESSIONS_WORKTREE.md.

Комплемент, не дубль: tools/cutover_doctor.check_no_foreign_markers сканирует
СЛОВА-маркеры (maksim/картинг/…) в АКТИВНЫХ panferov-файлах перед cutover. Здесь —
HEX-палитра в КОДЕ ядра, всегда. Слова-маркеры в ядре не грепаем: hyperframes_broll
исторически = движок дефолт-тенанта (Максим), там «Максим/картинг» легитимны; протечку
даёт именно ЦВЕТ, захардкоженный мимо контракта.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Tenant-нейтральные модули ядра, которые НЕ должны нести бренд-палитру.
# Расширять по мере выноса логики в core (каждый новый — отдельный prompt-builder).
CORE_MODULES = ["hyperframes_broll.py", "screen_broll.py", "auto_broll.py"]

# Универсальные цвета — не бренд-подпись (бел/чёрн встречаются легитимно везде,
# и оба тенанта делят белый текст). Их из набора стражимых исключаем.
_GENERIC = {"#FFFFFF", "#FFF", "#000000", "#000"}

_HEX6 = re.compile(r"#[0-9A-Fa-f]{6}\b")


def _brand_palette_values() -> set[str]:
    """Все hex-значения палитр из ОБОИХ контрактов, кроме универсальных.
    Источник правды — сами style_contract*.json: добавил тенанта → тест не трогаешь."""
    assets = ROOT / "hyperframes_assets"
    vals: set[str] = set()
    for jf in sorted(assets.glob("style_contract*.json")):
        pal = json.loads(jf.read_text(encoding="utf-8")).get("palette", {})
        for v in pal.values():
            if isinstance(v, str) and v.upper() not in _GENERIC:
                vals.add(v.upper())
    return vals


def test_brand_palettes_loaded():
    """Sanity: набор собран и оба брендовых акцента в нём (иначе тест слепой)."""
    vals = _brand_palette_values()
    assert "#FF5722" in vals, f"оранжевый акцент Максима не подхватился: {vals}"
    assert "#2E9BE0" in vals, f"синий акцент panferov не подхватился: {vals}"


@pytest.mark.parametrize("module", CORE_MODULES)
def test_core_has_no_brand_palette_hex(module):
    code = (ROOT / module).read_text(encoding="utf-8", errors="replace")
    brand = _brand_palette_values()
    found = {h.upper() for h in _HEX6.findall(code)}
    leaks = sorted(brand & found)
    assert not leaks, (
        f"{module}: бренд-палитра захардкожена в ядре: {leaks}. "
        f"Цвет должен приходить ТОЛЬКО через style_contract.<tenant>.json "
        f"(tenant.active_tenant_id() + inline_for_prompt), не литералом в коде."
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
