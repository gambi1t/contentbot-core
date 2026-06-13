"""TDD: panferov style_contract (Phase 2c→3 подготовка, вариант Б+В).

Готовим style_contract.panferov.json с палитрой Nox Dark (см.
memory reference_panferov_broll_palette.md), чтобы при пересадке panferov на
core (Phase 3) HyperFrames-движок включился через per-tenant контракт БЕЗ
порта кода. Движок (style_contract.py / hyperframes_broll.py) НЕ трогаем —
load_style_contract(path) уже принимает путь.

Тест доказывает: panferov-контракт (1) валиден по той же схеме, что Максимов;
(2) несёт палитру Nox Dark; (3) НЕ протёк оранжевым Максима (#FF5722);
(4) структурно совместим (inline_for_prompt / check_forbidden работают).

Run: python tests/test_style_contract_panferov.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from style_contract import (  # noqa: E402
    load_style_contract,
    inline_for_prompt,
    check_forbidden_in_html,
)

PANFEROV_PATH = ROOT / "hyperframes_assets" / "style_contract.panferov.json"


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_loads_and_valid_schema(errors):
    print("\n-- panferov-контракт грузится и валиден по схеме --")
    c = load_style_contract(PANFEROV_PATH)
    _assert(isinstance(c, dict), "контракт это dict", errors)
    _assert(c.get("version") == 1, f"version=1 (got {c.get('version')})", errors)
    for section in ("palette", "typography", "spacing", "frame",
                    "forbidden_labels", "forbidden_patterns_regex", "motion"):
        _assert(section in c, f"секция {section!r} присутствует", errors)


def test_palette_nox_dark(errors):
    print("\n-- palette = Nox Dark (navy + azure + белый, БЕЗ жёлтого) --")
    p = load_style_contract(PANFEROV_PATH).get("palette", {})
    _assert(p.get("bg_primary", "").upper() == "#0F172A",
            f"bg_primary=#0F172A (got {p.get('bg_primary')})", errors)
    _assert(p.get("bg_secondary", "").upper() == "#16213A",
            f"bg_secondary=#16213A (got {p.get('bg_secondary')})", errors)
    _assert(p.get("accent", "").upper() == "#2E9BE0",
            f"accent=#2E9BE0 (got {p.get('accent')})", errors)
    _assert(p.get("text_primary", "").upper() == "#F5F7FA",
            f"text_primary=#F5F7FA (got {p.get('text_primary')})", errors)
    _assert(p.get("text_muted", "").upper() == "#8A97AD",
            f"text_muted=#8A97AD (got {p.get('text_muted')})", errors)


def test_no_maksim_orange_leak(errors):
    print("\n-- НЕ протёк оранжевый Максима (#FF5722) и navy #0A0A0A --")
    c = load_style_contract(PANFEROV_PATH)
    block = inline_for_prompt(c).upper()
    _assert("#FF5722" not in block, "оранжевый Максима не в promtе panferov", errors)
    _assert("#0A0A0A" not in block, "чёрный фон Максима не в promtе panferov", errors)
    _assert("#2E9BE0" in block, "azure panferov в promtе", errors)
    _assert("#0F172A" in block, "navy panferov в promtе", errors)


def test_typography_inter_tight_inherited(errors):
    print("\n-- типографика Inter Tight унаследована (агент: оставить) --")
    t = load_style_contract(PANFEROV_PATH).get("typography", {})
    _assert(t.get("primary_family") == "Inter Tight",
            f"primary_family=Inter Tight (got {t.get('primary_family')})", errors)
    _assert(t.get("body_family") == "Inter",
            f"body_family=Inter (got {t.get('body_family')})", errors)


def test_frame_and_safe_area_inherited(errors):
    print("\n-- frame 1080×1920×5 и safe-area как у Максима --")
    c = load_style_contract(PANFEROV_PATH)
    f = c.get("frame", {})
    _assert(f.get("width") == 1080 and f.get("height") == 1920 and f.get("duration_s") == 5,
            f"frame 1080×1920×5 (got {f})", errors)
    sa = c.get("spacing", {}).get("safe_area", {})
    _assert(sa.get("x") == [40, 1040] and sa.get("y") == [480, 1440],
            f"safe-area как у Максима (got {sa})", errors)


def test_motion_blur_forbidden_inherited(errors):
    print("\n-- motion: запрет blur на тексте унаследован --")
    m = load_style_contract(PANFEROV_PATH).get("motion") or {}
    _assert(m.get("text_blur_forbidden") is True, "text_blur_forbidden=true", errors)
    _assert(set(m.get("text_entrance_allowed") or []) == {"opacity", "translate", "scale"},
            f"вход = opacity/translate/scale (got {m.get('text_entrance_allowed')})", errors)


def test_engine_functions_work_on_panferov(errors):
    print("\n-- inline_for_prompt / check_forbidden работают на panferov-контракте --")
    c = load_style_contract(PANFEROV_PATH)
    block = inline_for_prompt(c)
    _assert(isinstance(block, str) and len(block) > 200, "inline_for_prompt отдаёт текст", errors)
    _assert("1080" in block and "1920" in block, "размеры кадра в promtе", errors)
    bad = check_forbidden_in_html('<div>SCENE 06</div>', c)
    _assert(any("SCENE 06" in v for v in bad), "forbidden-логика работает (SCENE 06 пойман)", errors)


def main():
    print("=" * 60)
    print("test_style_contract_panferov (Phase 2c→3, Nox Dark)")
    print("=" * 60)
    errors: list[str] = []
    test_loads_and_valid_schema(errors)
    test_palette_nox_dark(errors)
    test_no_maksim_orange_leak(errors)
    test_typography_inter_tight_inherited(errors)
    test_frame_and_safe_area_inherited(errors)
    test_motion_blur_forbidden_inherited(errors)
    test_engine_functions_work_on_panferov(errors)
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
