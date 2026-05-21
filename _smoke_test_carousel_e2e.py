"""End-to-end smoke: Opus generates → renderer makes PNGs → visual audit.

Реальный сценарий теста Артёма: «топ-5 советов / 16 лет предприниматель…».
Проверяем что для темы с «топ-5» / «5 советов» автоматом получается 7 слайдов
(cover + 5 inner + CTA), cover имеет информативный subtitle (не банальный),
все 7 PNG читаемые.

Запускать локально из D:\\AI\\maksim-bot\\: `python _smoke_test_carousel_e2e.py`
Требует ANTHROPIC_API_KEY в env (.env или системный).
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Load .env so ANTHROPIC_API_KEY is available like the bot has it
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import anthropic
from carousel.llm import generate_carousel, infer_n_slides
from carousel.renderer import render_carousel

# ─────────────────────────────────────────────────────────────────────
# 1. Sanity: infer_n_slides on representative themes
# ─────────────────────────────────────────────────────────────────────
TEST_THEMES = [
    ("топ-5 советов для предпринимателя", 7),
    ("5 ошибок первого заезда на картинге", 7),
    ("3 правила выбора кофе", 5),
    ("10 фактов про глэмпинг", 10),  # capped to telegram limit
    ("как ездить на картинге", 7),    # no K → fallback 7
]

print("─── infer_n_slides sanity ───")
all_pass = True
for theme, expected in TEST_THEMES:
    got = infer_n_slides(theme)
    ok = got == expected
    mark = "✓" if ok else "✗"
    print(f"  {mark} «{theme}» → {got} (expected {expected})")
    if not ok:
        all_pass = False
if not all_pass:
    print("✗ infer_n_slides has bugs — fix before proceeding")
    sys.exit(1)
print("✓ infer_n_slides OK\n")

# ─────────────────────────────────────────────────────────────────────
# 2. Real Opus call + render with Артёмова's theme
# ─────────────────────────────────────────────────────────────────────
THEME = "5 советов от 16 лет предпринимательства: что я понял про деньги, людей и продукт"

print(f"─── E2E: Opus call ───")
print(f"  theme: {THEME!r}")

api_key = os.getenv("ANTHROPIC_API_KEY")
if not api_key:
    print("✗ ANTHROPIC_API_KEY not set in env / .env")
    sys.exit(2)
claude = anthropic.Anthropic(api_key=api_key)

try:
    slides = generate_carousel(claude, THEME)
except Exception as e:
    print(f"✗ generate_carousel failed: {type(e).__name__}: {e}")
    sys.exit(3)

print(f"  ✓ got {len(slides)} slides")
expected_n = infer_n_slides(THEME)
if len(slides) != expected_n:
    print(f"  ✗ expected {expected_n} slides, got {len(slides)} — Opus failed instruction")
    sys.exit(4)

# Check cover informativeness
cover = slides[0]
print(f"\n─── Cover audit ───")
print(f"  template:     {cover.get('template')}")
print(f"  issue_tag:    {cover.get('issue_tag')!r}")
print(f"  kicker:       {cover.get('kicker')!r}")
print(f"  hero:         {cover.get('hero')!r}")
print(f"  hero_word:    {cover.get('hero_word')!r}")
print(f"  title_main:   {cover.get('title_main')!r}")
print(f"  title_accent: {cover.get('title_accent')!r}")
print(f"  subtitle:     {cover.get('subtitle')!r}")
print(f"  counter:      {cover.get('counter')!r}")

# Heuristic: subtitle should be >40 chars (otherwise too generic)
sub = cover.get("subtitle", "")
if len(sub) < 40:
    print(f"  ✗ subtitle suspiciously short ({len(sub)} chars) — likely generic")
    sys.exit(5)
print(f"  ✓ subtitle has {len(sub)} chars — looks informative")

# Inner audit
print(f"\n─── Inner slides audit (slides 2..{len(slides)-1}) ───")
for i, sl in enumerate(slides[1:-1], start=2):
    print(f"  Slide {i}: kicker={sl.get('kicker')!r} | "
          f"title={sl.get('title')!r} | body={sl.get('body','')[:60]}...")

# CTA audit
cta = slides[-1]
print(f"\n─── CTA audit (slide {len(slides)}) ───")
print(f"  kicker:      {cta.get('kicker')!r}")
print(f"  title:       {cta.get('title')!r}")
print(f"  body:        {cta.get('body')!r}")

# ─────────────────────────────────────────────────────────────────────
# 3. Render to PNGs
# ─────────────────────────────────────────────────────────────────────
OUT = Path(__file__).parent / "clips-to-upload" / "_carousel_e2e_smoke"
print(f"\n─── Render PNGs → {OUT} ───")
try:
    pngs = render_carousel(slides, OUT)
except Exception as e:
    print(f"✗ render_carousel failed: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(6)

print(f"✓ {len(pngs)} PNGs rendered")
for p in pngs:
    size = p.stat().st_size
    print(f"  {p.name}  {size//1024} KB  {'✓' if size > 20_000 else '✗ TOO SMALL'}")
if not all(p.stat().st_size > 20_000 for p in pngs):
    print("✗ some PNGs too small")
    sys.exit(7)

print("\n✓✓✓ ALL CHECKS PASSED — карусель готова к деплою")
print(f"   откройте PNG для визуального аудита: {OUT}")
