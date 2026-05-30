"""Carousel renderer for Maksim/Life Drive brand.

Wraps the validated cover-template system from `_legacy_cover_generator.py`
(originally `D:\\AI\\studio_assets\\maksim_carousel_FINAL\\generate_final.py`,
locked 10 May 2026, design approved by Артём) and adds inner-slide rendering
+ a public `render_carousel(slides) -> list[Path]` API.

Key design:
- Cover slide (slide 1) uses one of M1/M2/M6 full templates with all decor.
- Inner slides (2..N) use a stripped-down text-on-bg template that inherits
  the cover's base color (dark for M1/M2, cream for M6) so the carousel
  reads as a coherent set, not a mismatched collage.
- One Playwright browser instance per carousel render — cold-start happens
  once (3-5 sec), each subsequent slide screenshot is ~1-2 sec.
- Google Fonts are loaded with `wait_for_load_state("networkidle") +
  wait_for_timeout(1500ms)` to guarantee Inter Tight Italic renders correctly
  (italic on heading is the brand marker — fallback to system font breaks LD).
"""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# ============================================================
# BRAND TOKENS — mirror of generate_final.py BRAND dict.
# Single source of truth for Life Drive across all rendering.
# Don't "improve" colors — see feedback_brand_separation_postulat_vs_panferov.md
# ============================================================
BRAND = {
    "red":    "#C8202A",
    "orange": "#F26622",
    "black":  "#0A0A0A",
    "white":  "#FFFFFF",
    "cream":  "#F4EFE6",
    "navy":   "#1A2238",
    "font_title": "'Inter Tight', sans-serif",
    "font_body":  "'Inter', sans-serif",
    "font_mono":  "'JetBrains Mono', monospace",
    "brand_handle": "@livedrive.tmn",
    "brand_name":   "LIVE DRIVE",
}

# Base color per template — needed for inner-slide rendering
BASE_COLOR = {
    "M1": "dark",   # black bg
    "M2": "dark",
    "M6": "cream",  # cream bg, navy text (sub-brand)
}

GOOGLE_FONTS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;0,500;0,600;0,700;0,800;0,900;1,800;1,900&family=Inter+Tight:ital,wght@0,800;0,900;1,800;1,900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
"""

BASE_RESET = """
*{box-sizing:border-box;margin:0;padding:0;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;}
html,body{width:1080px;height:1350px;overflow:hidden;}
.slide{width:1080px;height:1350px;position:relative;overflow:hidden;font-family:'Inter',sans-serif;}
"""


def wrap(css: str, body: str) -> str:
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"{GOOGLE_FONTS}<style>{BASE_RESET}{css}</style></head>"
        f'<body><div class="slide">{body}</div></body></html>'
    )


# ============================================================
# LIVE DRIVE wordmark badge — inline SVG flame-shape
# ============================================================
def ld_badge(variant: str = "color", w: int = 160) -> str:
    """variant: 'color' (red→orange gradient) | 'black' (solid black bg)

    viewBox 240×84 — flame расширен вправо, чтобы курсивная «Е» в DRIVE
    с запасом сидела внутри пламени (раньше при 200×84 буква лезла на
    правую дугу).
    """
    if variant == "color":
        fill = "url(#ldGrad)"
        defs = f'''<defs>
          <linearGradient id="ldGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="{BRAND['red']}"/>
            <stop offset="100%" stop-color="{BRAND['orange']}"/>
          </linearGradient>
        </defs>'''
        text_fill = BRAND['white']
    else:
        fill = BRAND['black']
        defs = ''
        text_fill = BRAND['white']
    h = int(w * 0.35)
    return f'''
<svg width="{w}" height="{h}" viewBox="0 0 240 84" xmlns="http://www.w3.org/2000/svg" style="display:block">
  {defs}
  <path d="M8,12 Q8,4 18,4 L208,4 Q220,4 224,14 Q234,28 230,42 Q234,56 220,72 Q214,80 204,80 L18,80 Q8,80 8,72 Z" fill="{fill}"/>
  <text x="22" y="54" font-family="Inter Tight, Inter, sans-serif" font-weight="900" font-style="italic" font-size="34" fill="{text_fill}" letter-spacing="-1">LIVE DRIVE</text>
</svg>'''


# ============================================================
# Cover renderers — copied verbatim from _legacy_cover_generator.py
# Don't refactor for "cleanliness" — these are visually-locked designs.
# ============================================================
def render_m1(t: dict) -> str:
    """M1 Anniversary Rings — для анонсов событий, юбилеев."""
    is_date = len(t["hero"]) > 2
    hero_size = "320px" if is_date else "520px"
    hero_top = "300px" if is_date else "240px"
    hero_letter = "-10px" if is_date else "-22px"
    word_visible = t.get("hero_word") is not None
    css = f"""
.slide{{background:{BRAND['black']};color:{BRAND['white']};}}
.rings{{position:absolute;right:-180px;top:140px;width:780px;height:780px;}}
.ring{{position:absolute;border-radius:50%;border-style:solid;}}
.ring.r1{{width:560px;height:560px;left:0;top:60px;border:34px solid {BRAND['red']};opacity:0.95;}}
.ring.r2{{width:560px;height:560px;left:200px;top:200px;border:34px solid {BRAND['orange']};opacity:0.95;mix-blend-mode:screen;}}
.ring.r3{{width:380px;height:380px;left:120px;top:0;border:22px solid {BRAND['orange']};opacity:0.75;}}
.brand{{position:absolute;top:54px;left:60px;z-index:5;}}
.tag{{position:absolute;top:64px;right:60px;font-family:{BRAND['font_mono']};font-size:14px;letter-spacing:3px;color:{BRAND['white']};font-weight:500;z-index:5;}}
.kicker{{position:absolute;left:60px;top:200px;font-family:{BRAND['font_mono']};font-size:18px;letter-spacing:5px;color:{BRAND['orange']};font-weight:700;text-transform:uppercase;z-index:5;}}
.hero{{position:absolute;left:40px;top:{hero_top};font-family:{BRAND['font_title']};font-style:italic;font-weight:900;font-size:{hero_size};line-height:0.85;color:{BRAND['white']};letter-spacing:{hero_letter};z-index:6;text-shadow:0 0 40px rgba(200,32,42,0.3);}}
.hero-word{{position:absolute;left:340px;top:340px;font-family:{BRAND['font_title']};font-style:italic;font-weight:900;font-size:104px;line-height:1;color:{BRAND['white']};letter-spacing:-3px;z-index:6;{'' if word_visible else 'display:none;'}}}
.bigtitle{{position:absolute;left:60px;top:790px;right:60px;font-family:{BRAND['font_title']};font-style:italic;font-weight:900;font-size:96px;line-height:0.95;color:{BRAND['white']};letter-spacing:-1px;word-spacing:0.08em;z-index:6;}}
.bigtitle .accent{{background:linear-gradient(90deg,{BRAND['red']},{BRAND['orange']});-webkit-background-clip:text;background-clip:text;color:transparent;}}
.subtitle{{position:absolute;left:60px;right:380px;bottom:130px;font-family:{BRAND['font_body']};font-style:italic;font-weight:500;font-size:24px;line-height:1.35;color:rgba(255,255,255,0.75);z-index:5;}}
.footer{{position:absolute;bottom:0;left:0;right:0;height:64px;border-top:1px solid rgba(255,255,255,0.12);display:flex;align-items:center;justify-content:space-between;padding:0 60px;font-family:{BRAND['font_mono']};font-size:15px;color:rgba(255,255,255,0.7);letter-spacing:2px;font-weight:500;}}
.footer .dot{{width:8px;height:8px;background:{BRAND['orange']};display:inline-block;margin-right:10px;vertical-align:middle;}}
"""
    body = f"""
<div class="rings"><div class="ring r1"></div><div class="ring r3"></div><div class="ring r2"></div></div>
<div class="brand">{ld_badge('color', 140)}</div>
<div class="tag">{t['issue_tag']}</div>
<div class="kicker">{t['kicker']}</div>
<div class="hero">{t['hero']}</div>
<div class="hero-word">{t['hero_word'] or ''}</div>
<div class="bigtitle">{t['title_main']}<br><span class="accent">{t['title_accent']}</span></div>
<div class="subtitle">{t['subtitle']}</div>
<div class="footer">
  <span><span class="dot"></span>{t['handle']}</span>
  <span>{t['counter']}</span>
</div>
"""
    return wrap(css, body)


def render_m2(t: dict) -> str:
    """M2 Pit-Stop Editorial — для гайдов, TOP-N, разборов (racing-feel)."""
    is_date = len(t["hero"]) > 2
    hero_size = "200px" if is_date else "340px"
    hero_letter = "-6px" if is_date else "-14px"
    word_visible = t.get("hero_word") is not None
    css = f"""
.slide{{background:{BRAND['black']};color:{BRAND['white']};}}
.tape{{position:absolute;left:0;right:0;top:560px;height:54px;background:linear-gradient(90deg,{BRAND['red']} 0%,{BRAND['orange']} 100%);z-index:2;}}
.tape2{{position:absolute;left:0;right:0;top:630px;height:6px;background:linear-gradient(90deg,{BRAND['orange']} 0%,{BRAND['red']} 100%);z-index:2;opacity:0.55;}}
.tape .tape-text{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:32px;font-family:{BRAND['font_mono']};font-size:18px;font-weight:700;color:{BRAND['black']};letter-spacing:6px;text-transform:uppercase;}}
.check{{position:absolute;left:0;right:0;bottom:0;height:34px;background:repeating-linear-gradient(90deg,{BRAND['white']} 0,{BRAND['white']} 34px,{BRAND['black']} 34px,{BRAND['black']} 68px);z-index:3;}}
.brand{{position:absolute;top:54px;left:60px;z-index:6;}}
.issue{{position:absolute;top:66px;right:60px;font-family:{BRAND['font_mono']};font-size:14px;letter-spacing:3px;color:{BRAND['white']};font-weight:500;z-index:6;}}
.eyebrow{{position:absolute;left:60px;top:200px;font-family:{BRAND['font_mono']};font-size:16px;letter-spacing:5px;color:{BRAND['orange']};font-weight:700;text-transform:uppercase;z-index:5;}}
.hero{{position:absolute;left:60px;top:230px;font-family:{BRAND['font_title']};font-style:italic;font-weight:900;font-size:{hero_size};line-height:0.88;letter-spacing:{hero_letter};z-index:5;background:linear-gradient(135deg,{BRAND['red']},{BRAND['orange']});-webkit-background-clip:text;background-clip:text;color:transparent;padding-right:30px;}}
.stacked{{position:absolute;left:430px;top:260px;z-index:5;font-family:{BRAND['font_title']};font-style:italic;font-weight:900;line-height:0.95;color:{BRAND['white']};letter-spacing:-2px;{'' if word_visible else 'display:none;'}}}
.stacked .top{{font-size:78px;}}
.stacked .word{{font-size:120px;margin-top:6px;}}
.maintitle{{position:absolute;left:60px;right:60px;top:700px;font-family:{BRAND['font_title']};font-style:italic;font-weight:900;font-size:108px;line-height:0.92;color:{BRAND['white']};letter-spacing:-2px;word-spacing:0.06em;z-index:4;}}
.maintitle .l2{{display:block;color:{BRAND['orange']};}}
.subtitle{{position:absolute;left:60px;right:60px;bottom:90px;font-family:{BRAND['font_body']};font-style:italic;font-weight:500;font-size:24px;line-height:1.35;color:rgba(255,255,255,0.75);z-index:5;max-width:780px;}}
.counter{{position:absolute;right:60px;top:200px;font-family:{BRAND['font_mono']};font-size:16px;letter-spacing:3px;color:rgba(255,255,255,0.6);font-weight:500;z-index:5;}}
"""
    body = f"""
<div class="tape"><div class="tape-text">RACE READY · LIVE DRIVE · TYUMEN · RACE READY · LIVE DRIVE</div></div>
<div class="tape2"></div>
<div class="brand">{ld_badge('color', 140)}</div>
<div class="issue">{t['issue_tag']}</div>
<div class="eyebrow">{t['kicker']}</div>
<div class="counter">{t['counter']}</div>
<div class="hero">{t['hero']}</div>
<div class="stacked"><div class="word">{t['hero_word'] or ''}</div></div>
<div class="maintitle">{t['title_main']}<span class="l2">{t['title_accent']}</span></div>
<div class="subtitle">{t['subtitle']} · {t['handle']}</div>
<div class="check"></div>
"""
    return wrap(css, body)


def render_m6(t: dict) -> str:
    """M6 Outdoor Quiet — sub-brand для глэмпинга/сапов/природы (cream bg)."""
    # Note: ICONS dict not implemented in this MVP wrapper — M6 cover skipped
    # for MVP demo. Add later when SVG icons (tent/sup/etc) are needed.
    raise NotImplementedError(
        "M6 cover not in MVP — only M1/M2 supported. Add ICONS dict + render_m6 when needed."
    )


TEMPLATE_RENDERERS = {
    "M1": render_m1,
    "M2": render_m2,
    # "M6": render_m6,  # MVP: skipped, add when SMM gives glamping content
}


# ============================================================
# Графическая система inner-слайдов (slide_type A/B/C)
# ============================================================
# Перенос из прототипа _proto_typography.py — формат принят Артёмом
# 17 мая («начнём в таком формате, по надобности доработаем», 6/10).
#   A — Statement (тезис): гигантская типографика + ghost-цифра
#   B — Breakdown (пункт): крупный индекс-номер + title + body
#   C — Quote (вывод): pull-quote с красной линейкой
# Графприёмы: ghost-цифра за краем кадра, speed-rails (диагональные
# racing-линии), шахматный финиш-мотив, telemetry-strip прогресса серии.
# Всё рисуется CSS/SVG — ноль внешних зависимостей.
# ============================================================

def _graphics_css() -> str:
    """Общий CSS графсистемы — ghost / rails / checker / telemetry / footer."""
    return f"""
.gslide{{background:{BRAND['black']};color:{BRAND['white']};}}
.ghost{{position:absolute;font-family:{BRAND['font_title']};font-style:italic;
  font-weight:900;color:{BRAND['white']};line-height:0.7;z-index:0;
  user-select:none;letter-spacing:-0.04em;}}
.rails{{position:absolute;height:200px;transform-origin:left center;
  z-index:1;pointer-events:none;}}
.rail{{position:absolute;left:0;border-radius:5px;}}
.rail.r1{{top:0;height:15px;width:100%;background:linear-gradient(90deg,
  rgba(200,32,42,0) 0%,{BRAND['red']} 40%,{BRAND['red']} 100%);}}
.rail.r2{{top:52px;height:8px;width:84%;background:repeating-linear-gradient(
  90deg,{BRAND['orange']} 0 32px,rgba(242,102,34,0) 32px 56px);}}
.rail.r3{{top:88px;height:5px;width:66%;background:linear-gradient(90deg,
  rgba(255,255,255,0) 0%,rgba(255,255,255,0.45) 55%,rgba(255,255,255,0.45) 100%);}}
.checker{{background-color:{BRAND['black']};
  background-image:linear-gradient(45deg,{BRAND['white']} 25%,transparent 25% 75%,{BRAND['white']} 75%),
    linear-gradient(45deg,{BRAND['white']} 25%,transparent 25% 75%,{BRAND['white']} 75%);
  background-size:24px 24px;background-position:0 0,12px 12px;}}
.telemetry{{position:absolute;left:0;right:0;bottom:64px;height:6px;
  display:flex;gap:4px;z-index:6;}}
.seg{{flex:1;height:100%;}}
.seg.passed{{background:{BRAND['red']};}}
.seg.current{{background:{BRAND['orange']};}}
.seg.future{{background:#2A2A2A;}}
.gfooter{{position:absolute;bottom:0;left:0;right:0;height:64px;
  border-top:1px solid rgba(255,255,255,0.12);display:flex;align-items:center;
  justify-content:space-between;padding:0 60px;font-family:{BRAND['font_mono']};
  font-size:15px;color:rgba(255,255,255,0.85);letter-spacing:2px;
  font-weight:500;z-index:6;}}
.gfooter .dot{{width:8px;height:8px;background:{BRAND['orange']};
  display:inline-block;margin-right:10px;vertical-align:middle;}}
.gbrand{{position:absolute;top:54px;left:60px;z-index:6;}}
.gtag{{position:absolute;top:66px;right:60px;font-family:{BRAND['font_mono']};
  font-size:14px;letter-spacing:3px;color:rgba(255,255,255,0.7);
  font-weight:500;z-index:6;}}
.geyebrow{{font-family:{BRAND['font_mono']};font-size:18px;letter-spacing:5px;
  color:{BRAND['orange']};font-weight:700;text-transform:uppercase;}}
"""


def _telemetry(current: int, total: int) -> str:
    """Полоса прогресса серии — `total` сегментов, `current` (0-based) активный."""
    segs = []
    for i in range(max(total, 1)):
        cls = "passed" if i < current else "current" if i == current else "future"
        segs.append(f'<div class="seg {cls}"></div>')
    return f'<div class="telemetry">{"".join(segs)}</div>'


def _gfooter(counter: str, handle: str) -> str:
    return (
        f'<div class="gfooter"><span><span class="dot"></span>{handle}</span>'
        f"<span>{counter}</span></div>"
    )


def _accent_html(text: str, accent_word: "str | None") -> str:
    """Подсветить accent_word в тексте span'ом .accent (один раз)."""
    if accent_word and accent_word in text:
        return text.replace(
            accent_word, f'<span class="accent">{accent_word}</span>', 1,
        )
    return text


def _inner_statement(slide: dict, idx: int, total: int) -> str:
    """Тип A — Statement: тезис гигантской типографикой + ghost-цифра."""
    ghost = f"{idx:02d}"
    title_html = _accent_html(slide.get("title", ""), slide.get("accent_word"))
    tag = slide.get("issue_tag") or BRAND["brand_name"]
    css = f"""
.slide{{background:{BRAND['black']};color:{BRAND['white']};}}
{_graphics_css()}
.ghost{{font-size:520px;right:-80px;bottom:-150px;opacity:0.055;}}
.rails{{top:520px;left:-150px;width:1320px;opacity:0.8;transform:rotate(-24deg);}}
.a-checker{{position:absolute;left:60px;top:150px;width:96px;height:22px;z-index:5;}}
.a-eyebrow{{position:absolute;left:60px;top:196px;z-index:5;max-width:940px;}}
.a-title{{position:absolute;left:58px;right:80px;top:268px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:104px;line-height:0.95;letter-spacing:-2px;color:{BRAND['white']};
  z-index:5;text-shadow:0 0 48px rgba(200,32,42,0.28);}}
.a-title .accent{{color:{BRAND['orange']};}}
.a-body{{position:absolute;left:62px;right:140px;top:760px;
  font-family:{BRAND['font_body']};font-style:italic;font-weight:500;
  font-size:33px;line-height:1.42;color:rgba(255,255,255,0.78);z-index:5;}}
.a-body .lead{{display:block;width:54px;height:5px;background:{BRAND['red']};
  margin-bottom:24px;}}
"""
    body = f"""
<div class="ghost">{ghost}</div>
<div class="rails"><div class="rail r1"></div><div class="rail r2"></div>
  <div class="rail r3"></div></div>
<div class="gbrand">{ld_badge('color', 140)}</div>
<div class="gtag">{tag}</div>
<div class="checker a-checker"></div>
<div class="geyebrow a-eyebrow">{slide.get('kicker', '')}</div>
<div class="a-title">{title_html}</div>
<div class="a-body"><span class="lead"></span>{slide.get('body', '')}</div>
{_telemetry(idx - 1, total)}
{_gfooter(slide.get('counter', ''), slide.get('handle', BRAND['brand_handle']))}
"""
    return wrap(css, body)


def _inner_breakdown(slide: dict, idx: int, total: int) -> str:
    """Тип B — Breakdown: крупный индекс-номер пункта + title + body."""
    ghost = f"{idx:02d}"
    # Номер пункта: первый inner-слайд (idx=2) — пункт 01.
    item_no = f"{max(idx - 1, 1):02d}"
    title_html = _accent_html(slide.get("title", ""), slide.get("accent_word"))
    tag = slide.get("issue_tag") or BRAND["brand_name"]
    css = f"""
.slide{{background:{BRAND['black']};color:{BRAND['white']};}}
{_graphics_css()}
.ghost{{font-size:600px;right:-50px;top:-195px;opacity:0.05;}}
/* Индекс выровнен по ПРАВОМУ краю зоны (left:0..width) — тогда узкая «01»
   и широкая «08»/«03» заканчиваются в одной точке и не наезжают на title. */
.b-index{{position:absolute;left:0;top:404px;width:412px;text-align:right;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:300px;line-height:0.78;letter-spacing:-9px;z-index:4;
  background:linear-gradient(150deg,{BRAND['red']},{BRAND['orange']});
  -webkit-background-clip:text;background-clip:text;color:transparent;}}
.b-kicker{{position:absolute;left:462px;top:312px;z-index:5;max-width:560px;}}
.b-title{{position:absolute;left:460px;right:74px;top:356px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:80px;line-height:0.98;letter-spacing:-1px;color:{BRAND['white']};z-index:5;}}
.b-title .accent{{color:{BRAND['orange']};}}
.b-divider{{position:absolute;left:462px;top:712px;width:120px;height:5px;
  background:{BRAND['red']};z-index:5;}}
.b-body{{position:absolute;left:462px;right:88px;top:760px;
  font-family:{BRAND['font_body']};font-style:italic;font-weight:500;
  font-size:32px;line-height:1.44;color:rgba(255,255,255,0.78);z-index:5;}}
"""
    body = f"""
<div class="ghost">{ghost}</div>
<div class="gbrand">{ld_badge('color', 140)}</div>
<div class="gtag">{tag}</div>
<div class="b-index">{item_no}</div>
<div class="geyebrow b-kicker">{slide.get('kicker', '')}</div>
<div class="b-title">{title_html}</div>
<div class="b-divider"></div>
<div class="b-body">{slide.get('body', '')}</div>
{_telemetry(idx - 1, total)}
{_gfooter(slide.get('counter', ''), slide.get('handle', BRAND['brand_handle']))}
"""
    return wrap(css, body)


def _inner_quote(slide: dict, idx: int, total: int) -> str:
    """Тип C — Quote: pull-quote крупным блоком с red→orange линейкой."""
    ghost = f"{idx:02d}"
    quote = (slide.get("pull_quote") or slide.get("title")
             or slide.get("body") or "")
    quote_html = _accent_html(quote, slide.get("accent_word"))
    tag = slide.get("issue_tag") or BRAND["brand_name"]
    css = f"""
.slide{{background:{BRAND['black']};color:{BRAND['white']};}}
{_graphics_css()}
.ghost{{font-size:620px;left:-100px;bottom:-165px;opacity:0.05;}}
.rails{{top:250px;left:560px;width:720px;opacity:0.6;transform:rotate(-24deg);}}
.c-checker{{position:absolute;left:150px;top:330px;width:96px;height:22px;z-index:5;}}
.c-bar{{position:absolute;left:90px;top:430px;width:9px;height:500px;
  background:linear-gradient(180deg,{BRAND['red']},{BRAND['orange']});z-index:5;}}
.c-quote{{position:absolute;left:150px;right:108px;top:424px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:800;
  font-size:66px;line-height:1.16;letter-spacing:-1px;color:{BRAND['white']};z-index:5;}}
.c-quote .accent{{color:{BRAND['orange']};}}
.c-attr{{position:absolute;left:150px;top:980px;font-family:{BRAND['font_mono']};
  font-size:17px;letter-spacing:3px;color:rgba(255,255,255,0.5);
  font-weight:500;z-index:5;}}
.c-attr b{{color:{BRAND['orange']};font-weight:700;}}
"""
    body = f"""
<div class="ghost">{ghost}</div>
<div class="rails"><div class="rail r1"></div><div class="rail r2"></div>
  <div class="rail r3"></div></div>
<div class="gbrand">{ld_badge('color', 140)}</div>
<div class="gtag">{tag}</div>
<div class="checker c-checker"></div>
<div class="c-bar"></div>
<div class="c-quote">{quote_html}</div>
<div class="c-attr"><b>//</b>&nbsp;&nbsp;{slide.get('kicker') or 'Вывод серии'}</div>
{_telemetry(idx - 1, total)}
{_gfooter(slide.get('counter', ''), slide.get('handle', BRAND['brand_handle']))}
"""
    return wrap(css, body)


_GRAPHICS_TILES = {
    "A": _inner_statement,
    "B": _inner_breakdown,
    "C": _inner_quote,
}


# ============================================================
# Inner-slide renderer (slides 2..N — text-only)
# ============================================================
def render_inner(
    slide: dict,
    base_template: str = "M2",
    bg_photo: "Path | str | None" = None,
    photo_layout: str = "fullbleed",
    slide_index: int = 2,
    total_slides: int = 7,
) -> str:
    """Inner-slide template — kicker + big italic title + body + footer.

    Inherits color theme from cover's base_template — dark (M1/M2) or
    cream (M6).

    bg_photo: optional B-roll photo path (dark templates only).
    photo_layout: how the photo is composed into the slide —
        "fullbleed" — photo on the whole slide, heavy black veil, text on top
        "split"     — photo as a hero block on top ~46%, text on dark below
        "diagonal"  — photo block with a slanted bottom edge (racing-feel)
    If bg_photo is None, layout is ignored and a plain dark slide is rendered.

    Slide schema: kicker / title / body / counter / handle, optional accent_word.
    """
    # Графсистема A/B/C — приоритетный путь (формат принят 17 мая).
    # LLM кладёт slide_type в каждый inner-слайд; рендерим типизированный тайл.
    stype = (slide.get("slide_type") or "").strip().upper()
    if stype in _GRAPHICS_TILES:
        return _GRAPHICS_TILES[stype](slide, slide_index, total_slides)

    # Backward-compat: слайды без slide_type → старый фото-путь.
    base = BASE_COLOR.get(base_template, "dark")
    if base == "cream":
        bg = BRAND['cream']
        fg = BRAND['navy']
        fg_muted = "rgba(26,34,56,0.75)"
        border = "rgba(26,34,56,0.15)"
        eyebrow_color = BRAND['red']
        accent_color = BRAND['red']
        badge_variant = "black"
        dot_color = BRAND['red']
    else:
        bg = BRAND['black']
        fg = BRAND['white']
        fg_muted = "rgba(255,255,255,0.75)"
        border = "rgba(255,255,255,0.12)"
        eyebrow_color = BRAND['orange']
        accent_color = BRAND['orange']
        badge_variant = "color"
        dot_color = BRAND['orange']

    accent_word = slide.get("accent_word")
    title_html = slide["title"]
    if accent_word and accent_word in title_html:
        title_html = title_html.replace(
            accent_word, f'<span class="accent">{accent_word}</span>', 1,
        )

    has_photo = bool(bg_photo) and base != "cream"
    photo_uri = Path(bg_photo).as_uri() if has_photo else None

    common = dict(
        slide=slide, bg=bg, fg=fg, fg_muted=fg_muted, border=border,
        eyebrow_color=eyebrow_color, accent_color=accent_color,
        badge_variant=badge_variant, dot_color=dot_color, title_html=title_html,
        photo_uri=photo_uri,
    )

    if has_photo and photo_layout == "split":
        return _inner_split(**common)
    if has_photo and photo_layout == "diagonal":
        return _inner_diagonal(**common)
    return _inner_fullbleed(**common)


def _inner_fullbleed(slide, bg, fg, fg_muted, border, eyebrow_color,
                     accent_color, badge_variant, dot_color, title_html,
                     photo_uri) -> str:
    """Photo on the whole slide, heavy black veil, text on top.

    photo_uri None → plain dark slide.
    """
    photo_layers = ""
    if photo_uri:
        photo_layers = (
            f'<div class="bg-photo" '
            f'style="background-image:url(&quot;{photo_uri}&quot;)"></div>'
            f'<div class="bg-veil"></div>'
        )
    css = f"""
.slide{{background:{bg};color:{fg};}}
.bg-photo{{position:absolute;inset:0;z-index:0;background-size:cover;background-position:center;}}
.bg-veil{{position:absolute;inset:0;z-index:1;background:linear-gradient(180deg,rgba(10,10,10,0.84) 0%,rgba(10,10,10,0.90) 50%,rgba(10,10,10,0.95) 100%);}}
.brand{{position:absolute;top:54px;left:60px;z-index:5;}}
.tag{{position:absolute;top:66px;right:60px;font-family:{BRAND['font_mono']};font-size:14px;letter-spacing:3px;color:{fg};opacity:0.7;font-weight:500;z-index:5;}}
.eyebrow{{position:absolute;left:60px;top:230px;font-family:{BRAND['font_mono']};font-size:18px;letter-spacing:5px;color:{eyebrow_color};font-weight:700;text-transform:uppercase;z-index:5;max-width:920px;}}
.title{{position:absolute;left:60px;right:60px;top:320px;font-family:{BRAND['font_title']};font-style:italic;font-weight:900;font-size:104px;line-height:0.94;color:{fg};letter-spacing:-1px;word-spacing:0.08em;z-index:5;}}
.title .accent{{color:{accent_color};}}
.body{{position:absolute;left:60px;right:60px;top:830px;font-family:{BRAND['font_body']};font-style:italic;font-weight:500;font-size:32px;line-height:1.4;color:{fg_muted};z-index:5;max-width:960px;}}
.footer{{position:absolute;bottom:0;left:0;right:0;height:64px;border-top:1px solid {border};display:flex;align-items:center;justify-content:space-between;padding:0 60px;font-family:{BRAND['font_mono']};font-size:15px;color:{fg};opacity:0.85;letter-spacing:2px;font-weight:500;}}
.footer .dot{{width:8px;height:8px;background:{dot_color};display:inline-block;margin-right:10px;vertical-align:middle;}}
"""
    body = f"""
{photo_layers}
<div class="brand">{ld_badge(badge_variant, 140)}</div>
<div class="tag">{slide.get('issue_tag', '')}</div>
<div class="eyebrow">{slide['kicker']}</div>
<div class="title">{title_html}</div>
<div class="body">{slide['body']}</div>
<div class="footer">
  <span><span class="dot"></span>{slide.get('handle', BRAND['brand_handle'])}</span>
  <span>{slide['counter']}</span>
</div>
"""
    return wrap(css, body)


def _inner_split(slide, bg, fg, fg_muted, border, eyebrow_color,
                 accent_color, badge_variant, dot_color, title_html,
                 photo_uri) -> str:
    """Photo as a hero block on top ~46%, text on dark below.

    Editorial-style — photo «вписан» as a composition element, not a veil.
    A bottom fade-gradient blends the photo into the dark text zone.
    """
    css = f"""
.slide{{background:{bg};color:{fg};}}
.photo-block{{position:absolute;top:0;left:0;right:0;height:624px;z-index:1;background-size:cover;background-position:center;}}
.photo-fade{{position:absolute;top:424px;left:0;right:0;height:240px;z-index:2;background:linear-gradient(180deg,rgba(10,10,10,0) 0%,rgba(10,10,10,0.55) 55%,{BRAND['black']} 100%);}}
.photo-vig{{position:absolute;top:0;left:0;right:0;height:220px;z-index:2;background:linear-gradient(180deg,rgba(10,10,10,0.55) 0%,rgba(10,10,10,0) 100%);}}
.brand{{position:absolute;top:54px;left:60px;z-index:5;}}
.tag{{position:absolute;top:66px;right:60px;font-family:{BRAND['font_mono']};font-size:14px;letter-spacing:3px;color:{BRAND['white']};opacity:0.85;font-weight:500;z-index:5;}}
.eyebrow{{position:absolute;left:60px;top:672px;font-family:{BRAND['font_mono']};font-size:18px;letter-spacing:5px;color:{eyebrow_color};font-weight:700;text-transform:uppercase;z-index:5;max-width:960px;}}
.title{{position:absolute;left:60px;right:60px;top:724px;font-family:{BRAND['font_title']};font-style:italic;font-weight:900;font-size:90px;line-height:0.96;color:{fg};letter-spacing:-1px;word-spacing:0.08em;z-index:5;}}
.title .accent{{color:{accent_color};}}
.body{{position:absolute;left:60px;right:60px;top:1058px;font-family:{BRAND['font_body']};font-style:italic;font-weight:500;font-size:31px;line-height:1.4;color:{fg_muted};z-index:5;max-width:960px;}}
.footer{{position:absolute;bottom:0;left:0;right:0;height:64px;border-top:1px solid {border};display:flex;align-items:center;justify-content:space-between;padding:0 60px;font-family:{BRAND['font_mono']};font-size:15px;color:{fg};opacity:0.85;letter-spacing:2px;font-weight:500;}}
.footer .dot{{width:8px;height:8px;background:{dot_color};display:inline-block;margin-right:10px;vertical-align:middle;}}
"""
    body = f"""
<div class="photo-block" style="background-image:url(&quot;{photo_uri}&quot;)"></div>
<div class="photo-vig"></div>
<div class="photo-fade"></div>
<div class="brand">{ld_badge(badge_variant, 140)}</div>
<div class="tag">{slide.get('issue_tag', '')}</div>
<div class="eyebrow">{slide['kicker']}</div>
<div class="title">{title_html}</div>
<div class="body">{slide['body']}</div>
<div class="footer">
  <span><span class="dot"></span>{slide.get('handle', BRAND['brand_handle'])}</span>
  <span>{slide['counter']}</span>
</div>
"""
    return wrap(css, body)


def _inner_diagonal(slide, bg, fg, fg_muted, border, eyebrow_color,
                    accent_color, badge_variant, dot_color, title_html,
                    photo_uri) -> str:
    """Photo block on top with a slanted bottom edge — racing-feel.

    Same content layout as split, but the photo's lower border is a
    diagonal cut (clip-path) + a thin orange edge-line along the slant.
    """
    css = f"""
.slide{{background:{bg};color:{fg};}}
.photo-block{{position:absolute;top:0;left:0;right:0;height:680px;z-index:1;background-size:cover;background-position:center;clip-path:polygon(0 0,100% 0,100% 76%,0 100%);}}
.photo-edge{{position:absolute;top:0;left:0;right:0;height:680px;z-index:2;clip-path:polygon(0 100%,100% 76%,100% 78.5%,0 102.5%);background:linear-gradient(90deg,{BRAND['red']},{BRAND['orange']});}}
.photo-vig{{position:absolute;top:0;left:0;right:0;height:220px;z-index:2;background:linear-gradient(180deg,rgba(10,10,10,0.5) 0%,rgba(10,10,10,0) 100%);}}
.brand{{position:absolute;top:54px;left:60px;z-index:5;}}
.tag{{position:absolute;top:66px;right:60px;font-family:{BRAND['font_mono']};font-size:14px;letter-spacing:3px;color:{BRAND['white']};opacity:0.85;font-weight:500;z-index:5;}}
.eyebrow{{position:absolute;left:60px;top:716px;font-family:{BRAND['font_mono']};font-size:18px;letter-spacing:5px;color:{eyebrow_color};font-weight:700;text-transform:uppercase;z-index:5;max-width:960px;}}
.title{{position:absolute;left:60px;right:60px;top:766px;font-family:{BRAND['font_title']};font-style:italic;font-weight:900;font-size:86px;line-height:0.96;color:{fg};letter-spacing:-1px;word-spacing:0.08em;z-index:5;}}
.title .accent{{color:{accent_color};}}
.body{{position:absolute;left:60px;right:60px;top:1066px;font-family:{BRAND['font_body']};font-style:italic;font-weight:500;font-size:31px;line-height:1.4;color:{fg_muted};z-index:5;max-width:960px;}}
.footer{{position:absolute;bottom:0;left:0;right:0;height:64px;border-top:1px solid {border};display:flex;align-items:center;justify-content:space-between;padding:0 60px;font-family:{BRAND['font_mono']};font-size:15px;color:{fg};opacity:0.85;letter-spacing:2px;font-weight:500;}}
.footer .dot{{width:8px;height:8px;background:{dot_color};display:inline-block;margin-right:10px;vertical-align:middle;}}
"""
    body = f"""
<div class="photo-block" style="background-image:url(&quot;{photo_uri}&quot;)"></div>
<div class="photo-edge"></div>
<div class="photo-vig"></div>
<div class="brand">{ld_badge(badge_variant, 140)}</div>
<div class="tag">{slide.get('issue_tag', '')}</div>
<div class="eyebrow">{slide['kicker']}</div>
<div class="title">{title_html}</div>
<div class="body">{slide['body']}</div>
<div class="footer">
  <span><span class="dot"></span>{slide.get('handle', BRAND['brand_handle'])}</span>
  <span>{slide['counter']}</span>
</div>
"""
    return wrap(css, body)


# ============================================================
# Public API — render full carousel
# ============================================================
def render_carousel(
    slides: list[dict],
    out_dir: Path,
    bg_photos: "list[Path] | None" = None,
    photo_layout: str = "fullbleed",
) -> list[Path]:
    """Render an N-slide carousel as PNGs in `out_dir`.

    `slides[0]` is the cover (must have all cover-template fields:
    template / issue_tag / kicker / hero / hero_word / title_main /
    title_accent / subtitle / counter / handle).

    `slides[1..N-1]` are inner slides with the inner-slide schema
    (kicker / title / body / counter / handle, optional accent_word).

    bg_photos: optional list of B-roll photo paths for inner-slide
    backgrounds. Aligned to slides[1:] — bg_photos[k] is used for
    slides[k+1]. Cover (slide 0) never gets a photo. If None or shorter
    than the inner-slide count, missing slots render plain dark.

    Returns list of Path objects in order. One Playwright browser instance
    is reused across all slides (cold-start ~3-5 sec, then ~1-2 sec/slide).

    Raises ValueError if `slides` empty or cover template not supported.
    """
    if not slides:
        raise ValueError("empty slides list")
    cover = slides[0]
    if cover.get("template") not in TEMPLATE_RENDERERS:
        raise ValueError(
            f"unsupported cover template: {cover.get('template')!r}; "
            f"supported: {list(TEMPLATE_RENDERERS)}"
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_dir = out_dir / "_html"
    html_dir.mkdir(exist_ok=True)

    bg_photos = bg_photos or []

    # Render HTMLs
    base_template = cover["template"]
    html_paths: list[Path] = []
    for i, slide in enumerate(slides):
        if i == 0:
            html = TEMPLATE_RENDERERS[slide["template"]](slide)
        else:
            # bg_photos aligned to slides[1:] — index i-1
            photo = bg_photos[i - 1] if i - 1 < len(bg_photos) else None
            html = render_inner(
                slide, base_template, bg_photo=photo,
                photo_layout=photo_layout,
                slide_index=i + 1, total_slides=len(slides),
            )
        h_path = html_dir / f"slide_{i + 1:02d}.html"
        h_path.write_text(html, encoding="utf-8")
        html_paths.append(h_path)

    # Render PNGs (reuse browser)
    png_paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for i, h_path in enumerate(html_paths):
                page = browser.new_page(
                    viewport={"width": 1080, "height": 1350},
                    device_scale_factor=2,    # hi-res 2160×2700 PNG для IG-апскейла
                )
                page.goto(h_path.as_uri())
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(1500)  # belt-and-suspenders for fonts
                png = out_dir / f"slide_{i + 1:02d}.png"
                page.screenshot(
                    path=str(png),
                    clip={"x": 0, "y": 0, "width": 1080, "height": 1350},
                )
                page.close()
                png_paths.append(png)
                logger.info(f"[carousel] rendered slide {i + 1}/{len(slides)}: {png.name}")
        finally:
            browser.close()
    return png_paths
