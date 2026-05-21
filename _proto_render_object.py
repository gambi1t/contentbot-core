"""Prototype: render carousel inner-slides (variant A — hero bleed) with
different branded objects, to confirm the look holds across object types.

Run: python _proto_render_object.py  → PNGs in _proto_object_bank/slides/
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BRANDED = Path(__file__).parent / "_proto_object_bank" / "branded"
OUT = Path(__file__).parent / "_proto_object_bank" / "slides"
OUT.mkdir(parents=True, exist_ok=True)

if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BLACK = "#0A0A0A"
WHITE = "#FFFFFF"
ORANGE = "#F26622"
RED = "#C8202A"
MUTED = "rgba(255,255,255,0.75)"

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Inter+Tight:ital,wght@1,800;1,900&family=Inter:ital,wght@1,500&'
    'family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">'
)

# slug → (kicker, title-html, body, counter, object-width-px, right-px, bottom-px)
SLIDES = {
    "globe": (
        "ВЫВОД 03 · СИСТЕМА",
        f'ПОРЯДОК <span style="color:{ORANGE}">БЬЁТ ХАОС</span>',
        "Система — это не CRM и не таблицы. Это когда дело держит ритм "
        "без твоего ручного руля каждый день.",
        "04 / 07", 760, -130, -90,
    ),
    "clock": (
        "ВЫВОД 04 · ВРЕМЯ",
        f'СКОРОСТЬ <span style="color:{ORANGE}">РЕШАЕТ</span>',
        "Кто быстрее проверяет гипотезу — быстрее находит рабочую. "
        "Темп важнее идеальности первого шага.",
        "05 / 07", 560, 40, -70,
    ),
    "helmet": (
        "ВЫВОД 05 · РИСК",
        f'ЗАЩИТА <span style="color:{ORANGE}">ПЕРЕД ГОНКОЙ</span>',
        "Перед тем как давить газ — посчитай, чем рискуешь. "
        "Скорость без подушки безопасности — это не смелость.",
        "06 / 07", 600, -40, -40,
    ),
}


def render_variant_a(obj_uri, kicker, title, body, counter,
                     obj_w, obj_right, obj_bottom) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">{FONTS}
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
html,body{{width:1080px;height:1350px;overflow:hidden;}}
.slide{{width:1080px;height:1350px;position:relative;background:{BLACK};overflow:hidden;}}
.obj{{position:absolute;right:{obj_right}px;bottom:{obj_bottom}px;width:{obj_w}px;
  z-index:1;filter:drop-shadow(0 0 90px rgba(242,102,34,0.28));opacity:0.92;}}
.veil{{position:absolute;inset:0;z-index:2;background:linear-gradient(105deg,
  {BLACK} 30%,rgba(10,10,10,0.55) 56%,rgba(10,10,10,0.15) 100%);}}
.brand{{position:absolute;top:54px;left:60px;z-index:5;display:inline-flex;
  align-items:center;background:linear-gradient(120deg,{RED},{ORANGE});
  padding:11px 20px 13px;border-radius:8px;}}
.brand span{{font-family:'Inter Tight';font-style:italic;font-weight:900;
  font-size:26px;color:#fff;letter-spacing:-1px;}}
.kicker{{position:absolute;left:60px;top:250px;z-index:5;
  font-family:'JetBrains Mono';font-size:18px;letter-spacing:5px;
  color:{ORANGE};font-weight:700;}}
.title{{position:absolute;left:60px;top:312px;width:660px;z-index:5;
  font-family:'Inter Tight';font-style:italic;font-weight:900;font-size:104px;
  line-height:0.96;color:{WHITE};letter-spacing:-1px;}}
.body{{position:absolute;left:60px;bottom:230px;width:560px;z-index:5;
  font-family:'Inter';font-style:italic;font-weight:500;font-size:31px;
  line-height:1.4;color:{MUTED};}}
.footer{{position:absolute;bottom:0;left:0;right:0;height:64px;z-index:5;
  border-top:1px solid rgba(255,255,255,0.12);display:flex;align-items:center;
  justify-content:space-between;padding:0 60px;font-family:'JetBrains Mono';
  font-size:15px;color:rgba(255,255,255,0.85);letter-spacing:2px;}}
.dot{{width:8px;height:8px;background:{ORANGE};display:inline-block;
  margin-right:10px;}}
</style></head><body><div class="slide">
<img class="obj" src="{obj_uri}">
<div class="veil"></div>
<div class="brand"><span>LIVE DRIVE</span></div>
<div class="kicker">{kicker}</div>
<div class="title">{title}</div>
<div class="body">{body}</div>
<div class="footer"><span><span class="dot"></span>@livedrive.tmn</span>
<span>{counter}</span></div>
</div></body></html>"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for slug, (kicker, title, body, counter, ow, oright, obot) in SLIDES.items():
            obj = BRANDED / f"{slug}_duo.png"
            if not obj.exists():
                print(f"skip {slug} — no object")
                continue
            html = render_variant_a(obj.as_uri(), kicker, title, body,
                                    counter, ow, oright, obot)
            hp = OUT / f"A_{slug}.html"
            hp.write_text(html, encoding="utf-8")
            page = browser.new_page(
                viewport={"width": 1080, "height": 1350}, device_scale_factor=2,
            )
            page.goto(hp.as_uri())
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(1400)
            png = OUT / f"A_{slug}.png"
            page.screenshot(path=str(png),
                            clip={"x": 0, "y": 0, "width": 1080, "height": 1350})
            page.close()
            print(f"rendered A_{slug}.png  {png.stat().st_size // 1024} KB")
        browser.close()


if __name__ == "__main__":
    main()
