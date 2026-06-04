"""TDD: Style contract (Phase 1 Step 2 production-плана).

По ревью ChatGPT 4 июня: «для единства — нужен не done_scenes, а общий style
contract: palette, type scale, spacing, component rules, forbidden labels. Его
надо генерировать/фиксировать до сцен и передавать всем агентам.»

Контракт:
  contract = load_style_contract()                  # читает hyperframes_assets/style_contract.json
  block    = inline_for_prompt(contract)            # markdown-секция для встройки в _build_scene_prompt
  forbidden = check_forbidden_in_html(html, contract)  # → list[str] нарушений (или [] если чисто)

Структура style_contract.json:
  version: int
  palette: {bg_primary, bg_secondary, accent, text_primary, text_muted}
  typography: {hero/body/kicker/caption min/max px, primary_family, body_family, weights}
  spacing: {safe_area {x:[min,max], y:[min,max]}, gap_min_px, padding_px}
  frame: {width, height, duration_s}
  forbidden_labels: [literal strings]
  forbidden_patterns_regex: [regex strings]

Run: python tests/test_style_contract.py
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
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))
from style_contract import (  # noqa: E402
    load_style_contract,
    inline_for_prompt,
    check_forbidden_in_html,
)


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


# ── 1. загрузка ──────────────────────────────────────────────────────────
def test_load_contract(errors):
    print("\n-- load_style_contract читает hyperframes_assets/style_contract.json --")
    contract = load_style_contract()
    _assert(isinstance(contract, dict), "контракт это dict", errors)
    _assert(contract.get("version") == 1, f"version=1 (got {contract.get('version')})", errors)
    # обязательные секции
    for section in ("palette", "typography", "spacing", "frame",
                    "forbidden_labels", "forbidden_patterns_regex"):
        _assert(section in contract, f"секция {section!r} присутствует", errors)


def test_palette_brand_colors(errors):
    print("\n-- palette содержит брендовые цвета Максима --")
    c = load_style_contract()
    p = c.get("palette", {})
    _assert(p.get("accent", "").upper() == "#FF5722",
            f"accent=#FF5722 (got {p.get('accent')})", errors)
    _assert(p.get("bg_primary", "").upper() == "#0A0A0A",
            f"bg_primary=#0A0A0A (got {p.get('bg_primary')})", errors)
    _assert(p.get("text_primary", "").upper() == "#FFFFFF",
            f"text_primary=#FFFFFF (got {p.get('text_primary')})", errors)
    _assert(p.get("text_muted", "").upper() in ("#BBBBBB", "#BBB"),
            f"text_muted=#BBB(BBB) (got {p.get('text_muted')})", errors)


def test_safe_area(errors):
    print("\n-- safe_area соответствует reference_pack.md --")
    c = load_style_contract()
    sa = c.get("spacing", {}).get("safe_area", {})
    _assert(sa.get("x") == [40, 1040], f"x=[40,1040] (got {sa.get('x')})", errors)
    _assert(sa.get("y") == [480, 1440], f"y=[480,1440] (got {sa.get('y')})", errors)


def test_frame_1080x1920_5s(errors):
    print("\n-- frame: 1080×1920, 5 секунд --")
    c = load_style_contract()
    f = c.get("frame", {})
    _assert(f.get("width") == 1080, "width=1080", errors)
    _assert(f.get("height") == 1920, "height=1920", errors)
    _assert(f.get("duration_s") == 5, "duration_s=5", errors)


# ── 2. inline_for_prompt ─────────────────────────────────────────────────
def test_inline_block_contains_brand(errors):
    print("\n-- inline_for_prompt: бренд-секция с цветами в тексте --")
    block = inline_for_prompt(load_style_contract())
    _assert(isinstance(block, str), "inline_for_prompt возвращает str", errors)
    _assert("#FF5722" in block, "accent #FF5722 в тексте", errors)
    _assert("#0A0A0A" in block, "bg #0A0A0A в тексте", errors)
    _assert("1080" in block and "1920" in block, "размеры кадра в тексте", errors)
    # safe-area хотя бы числами
    _assert("40" in block and "1040" in block, "safe-area x числа", errors)
    _assert("480" in block and "1440" in block, "safe-area y числа", errors)


def test_inline_block_contains_forbidden_labels(errors):
    print("\n-- inline_for_prompt: явный запрет debug-меток --")
    block = inline_for_prompt(load_style_contract())
    # либо явный список запрещённых, либо явная фраза «НЕ добавляй»
    has_forbid = any(s in block.upper() for s in (
        "SCENE 01", "SCENE / 01", "FINAL CTA", "DEBUG"
    )) and any(s in block.lower() for s in (
        "не добавляй", "запрещ", "forbidden"
    ))
    _assert(has_forbid, "явный запрет debug-меток в тексте промпта", errors)


# ── 3. check_forbidden_in_html ───────────────────────────────────────────
def test_check_forbidden_catches_scene_label(errors):
    print("\n-- check_forbidden: ловит литерал SCENE 06 / SCENE / 01 --")
    contract = load_style_contract()
    bad1 = '<html><body><div class="meta">SCENE 06</div></body></html>'
    bad2 = '<html><body><div class="corner">SCENE / 01</div></body></html>'
    good = '<html><body><div>контент</div></body></html>'
    h1 = check_forbidden_in_html(bad1, contract)
    h2 = check_forbidden_in_html(bad2, contract)
    h3 = check_forbidden_in_html(good, contract)
    _assert(len(h1) > 0 and any("SCENE 06" in v for v in h1),
            f"SCENE 06 пойман (got {h1})", errors)
    _assert(len(h2) > 0 and any("SCENE / 01" in v for v in h2),
            f"SCENE / 01 пойман (got {h2})", errors)
    _assert(h3 == [], f"чистый html без нарушений (got {h3})", errors)


def test_check_forbidden_catches_final_cta(errors):
    print("\n-- check_forbidden: ловит FINAL CTA --")
    contract = load_style_contract()
    bad = '<div class="meta-accent">FINAL CTA</div>'
    found = check_forbidden_in_html(bad, contract)
    _assert(len(found) > 0 and any("FINAL CTA" in v for v in found),
            f"FINAL CTA пойман (got {found})", errors)


def test_check_forbidden_catches_regex_variant(errors):
    print("\n-- check_forbidden: regex ловит вариации (SCENE 02 of 06) --")
    contract = load_style_contract()
    # вариант который не в literal list, но в regex
    bad = '<div>scene 02 of 06</div>'
    found = check_forbidden_in_html(bad, contract)
    _assert(len(found) > 0, f"вариация поймана regex'ом (got {found})", errors)


def test_check_forbidden_ignores_legit_content(errors):
    print("\n-- check_forbidden: НЕ матчит content, упоминающий 'scene' в коде --")
    contract = load_style_contract()
    # ВНУТРИ scene_NN.html всегда есть data-composition-id="scene_NN" — это легитимно
    legit_html = '''<div id="scene_02" data-composition-id="scene_02"
                       data-width="1080">контент</div>'''
    found = check_forbidden_in_html(legit_html, contract)
    _assert(found == [],
            f"легитимный data-composition-id='scene_02' не должен ловиться (got {found})",
            errors)


# ── 4. интеграция со старым _build_scene_prompt ──────────────────────────
def test_orchestrator_uses_contract_in_prompt(errors):
    """Промпт _build_scene_prompt должен теперь содержать секцию из контракта."""
    print("\n-- _build_scene_prompt включает блок style contract --")
    import hyperframes_broll as H  # импорт только тут, чтобы не падать на пакетных вещах
    sb = {"scenes": [{"id": "scene_01", "business_archetype": "hero_number",
                      "hf_technique": "counter_animation",
                      "visual_style": "swiss_pulse",
                      "motion_family": "counter_build",
                      "density": "balanced", "scale_profile": "hero",
                      "primary_text": "X", "script_beat": "beat", "reason": "r"}]}
    prompt = H._build_scene_prompt(sb, "scene_01", [])
    # Признаки что используется контракт:
    # цвета из палетты упоминаются, и есть запрет debug-меток
    has_accent = "#FF5722" in prompt
    has_safe = ("40" in prompt and "1040" in prompt and
                "480" in prompt and "1440" in prompt)
    has_forbid = any(s in prompt.upper() for s in
                     ("SCENE 06", "SCENE / 01", "FINAL CTA"))
    _assert(has_accent, "accent в промпте", errors)
    _assert(has_safe, "safe-area числа в промпте", errors)
    _assert(has_forbid, "запрет debug-меток в промпте", errors)


def main():
    print("=" * 60)
    print("test_style_contract (Phase 1 step 2)")
    print("=" * 60)
    errors = []
    test_load_contract(errors)
    test_palette_brand_colors(errors)
    test_safe_area(errors)
    test_frame_1080x1920_5s(errors)
    test_inline_block_contains_brand(errors)
    test_inline_block_contains_forbidden_labels(errors)
    test_check_forbidden_catches_scene_label(errors)
    test_check_forbidden_catches_final_cta(errors)
    test_check_forbidden_catches_regex_variant(errors)
    test_check_forbidden_ignores_legit_content(errors)
    test_orchestrator_uses_contract_in_prompt(errors)
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
