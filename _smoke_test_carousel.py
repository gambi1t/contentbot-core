"""Local smoke: render_carousel on dummy slides, check PNG dimensions.

NOTE: This is a renderer-only smoke (no LLM call). For full LLM+render
end-to-end test, see _smoke_test_carousel_e2e.py (calls Opus).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from carousel.renderer import render_carousel

import io
if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DUMMY_SLIDES = [
    # Cover
    {
        "template": "M2",
        "issue_tag": "GUIDE №01 · МАЙ 2026",
        "kicker": "GUIDE · ПЕРВЫЙ ЗАЕЗД",
        "hero": "5",
        "hero_word": "ОШИБОК",
        "title_main": "ПЕРВОГО ЗАЕЗДА ",
        "title_accent": "НА КАРТИНГЕ",
        "subtitle": "из 16 лет в Life Drive · которых можно избежать",
        "counter": "01 / 05",
        "handle": "@livedrive.tmn",
    },
    # Inner 1
    {
        "kicker": "ОШИБКА 01 · СТАРТ",
        "title": "ГАЗ В ПОЛ С ПЕРВОЙ СЕКУНДЫ",
        "accent_word": "С ПЕРВОЙ СЕКУНДЫ",
        "body": "Первый круг — это разогрев шин, не гонка. Холодная резина — нулевой держак. Время теряется не там, где быстрый старт.",
        "counter": "02 / 05",
        "handle": "@livedrive.tmn",
    },
    # Inner 2
    {
        "kicker": "ОШИБКА 02 · ТОРМОЖЕНИЕ",
        "title": "ТОРМОЗИТЬ В ПОВОРОТЕ",
        "accent_word": "В ПОВОРОТЕ",
        "body": "Тормоз — только на прямой, ДО поворота. В вираже работает только газ и руль.",
        "counter": "03 / 05",
        "handle": "@livedrive.tmn",
    },
    # Inner 3
    {
        "kicker": "ОШИБКА 03 · ТРАЕКТОРИЯ",
        "title": "ЕХАТЬ ПО ВНУТРЕННЕЙ",
        "accent_word": "ВНУТРЕННЕЙ",
        "body": "Внешний радиус — длиннее по карте, но быстрее по времени. Это знают все гонщики и не знает ни один новичок.",
        "counter": "04 / 05",
        "handle": "@livedrive.tmn",
    },
    # CTA
    {
        "kicker": "ОТ АВТОРА · CTA",
        "title": "ХОЧЕШЬ БОЛЬШЕ?",
        "accent_word": "БОЛЬШЕ",
        "body": "Подпишись @livedrive.tmn — каждую неделю разборы из реального опыта картинг-центра и глэмпинга.",
        "counter": "05 / 05",
        "handle": "@livedrive.tmn",
    },
]

OUT = Path(__file__).parent / "clips-to-upload" / "_carousel_smoke"

print(f"→ render_carousel({len(DUMMY_SLIDES)} slides) → {OUT}")
try:
    pngs = render_carousel(DUMMY_SLIDES, OUT)
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print(f"✓ rendered {len(pngs)} PNGs")
for p in pngs:
    size = p.stat().st_size
    print(f"  {p.name}  {size//1024} KB  {'✓' if size > 20_000 else '✗ TOO SMALL'}")
if all(p.stat().st_size > 20_000 for p in pngs):
    print("✓ all PNGs >20KB — looks valid")
else:
    print("✗ some PNGs too small")
    sys.exit(2)
