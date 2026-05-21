"""
MAKSIM / LIVE DRIVE — FINAL carousel cover system (v1, locked 10 мая 2026).

Артём выбрал из 6 направлений v1 → 3 финальных формата:

  M1 · Anniversary Rings    — для АНОНСОВ и СОБЫТИЙ (продолжение шаблона 23/04)
  M2 · Pit-Stop Editorial   — для ГАЙДОВ / TOP-N / РАЗБОРОВ (racing-feel)
  M6 · Outdoor Quiet        — для ГЛЭМПИНГА / САПОВ (sub-brand, не картинг)

Темы которые рендерим для валидации:
  1. M1 · guide      → ТОП-5 фишек для первого заезда (TOP-N контент)
  2. M1 · announce   → 23/04 НАЧАЛО НОВОГО СЕЗОНА (анонс события — его настоящая сила)
  3. M2 · guide      → ТОП-5 фишек для первого заезда (то что выбрал Артём)
  4. M6 · glamping   → 5 ПРИЧИН ВЫБРАТЬ НАШ ГЛЭМПИНГ (sub-brand validation)

Размер: 1080×1350 (Instagram 4:5). Никаких 2000px+.

THEMES dict — единственное место где задаётся контент. Под новую тему — добавить
запись в THEMES и записать в VARIANTS какой template к ней применить.
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).parent
HTML_DIR = OUT_DIR / "html"
PNG_DIR = OUT_DIR / "png"
HTML_DIR.mkdir(exist_ok=True)
PNG_DIR.mkdir(exist_ok=True)

# ============================================================
# BRAND TOKENS — single source of truth for Life Drive
# ============================================================
BRAND = {
    # Colors (warm racing palette — НЕ путать с @panferov.ai cool)
    "red":        "#C8202A",   # primary red
    "orange":     "#F26622",   # primary orange (gradient end)
    "black":      "#0A0A0A",   # dark background
    "white":      "#FFFFFF",
    "cream":      "#F4EFE6",   # M6 outdoor sub-brand bg
    "navy":       "#1A2238",   # M6 text on cream
    # Fonts (all italic for racing feel)
    "font_title":   "'Inter Tight', sans-serif",
    "font_body":    "'Inter', sans-serif",
    "font_mono":    "'JetBrains Mono', monospace",
    # Handle
    "brand_handle": "@livedrive.tmn",
    "brand_name":   "LIVE DRIVE",
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
# LIVE DRIVE wordmark badge (inline SVG)
# ============================================================
def ld_badge(variant: str = "color", w: int = 140) -> str:
    """variant: 'color' (red→orange gradient) | 'black' (solid black bg)"""
    if variant == "color":
        fill = "url(#ldGrad)"
        defs = f'''<defs>
          <linearGradient id="ldGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="{BRAND['red']}"/>
            <stop offset="100%" stop-color="{BRAND['orange']}"/>
          </linearGradient>
        </defs>'''
        text_fill = BRAND['white']
    else:  # black
        fill = BRAND['black']
        defs = ''
        text_fill = BRAND['white']
    h = int(w * 0.42)
    return f'''
<svg width="{w}" height="{h}" viewBox="0 0 200 84" xmlns="http://www.w3.org/2000/svg" style="display:block">
  {defs}
  <path d="M8,12 Q8,4 18,4 L172,4 Q184,4 188,14 Q196,28 192,42 Q196,56 184,72 Q178,80 168,80 L18,80 Q8,80 8,72 Z" fill="{fill}"/>
  <text x="22" y="54" font-family="Inter Tight, Inter, sans-serif" font-weight="900" font-style="italic" font-size="34" fill="{text_fill}" letter-spacing="-1">LIVE DRIVE</text>
</svg>'''


# ============================================================
# ICONS — small brand-icons для M6 (outdoor sub-brand)
# Расширяемый dict: добавь новый icon — он становится доступен через t["icon"] в THEMES
# ============================================================
ICONS = {
    "tent": f'''
<svg viewBox="0 0 200 180" xmlns="http://www.w3.org/2000/svg">
  <!-- A-frame tent silhouette -->
  <path d="M18,165 L100,28 L182,165 Z" fill="{BRAND['red']}"/>
  <!-- door cutout (cream punch-through) -->
  <path d="M76,165 L100,98 L124,165 Z" fill="{BRAND['cream']}"/>
  <!-- ground line accent (orange) -->
  <line x1="10" y1="168" x2="190" y2="168" stroke="{BRAND['orange']}" stroke-width="5" stroke-linecap="round"/>
</svg>''',

    "sup": f'''
<svg viewBox="0 0 260 180" xmlns="http://www.w3.org/2000/svg">
  <!-- water line accent (orange) — board floating on water -->
  <line x1="6" y1="172" x2="254" y2="172" stroke="{BRAND['orange']}" stroke-width="5" stroke-linecap="round"/>
  <!-- SUP board (side-view, horizontal lozenge with rounded ends, long & flat) -->
  <path d="M30,138
           Q30,124 50,122
           L180,122
           Q220,122 240,138
           Q220,158 180,158
           L50,158
           Q30,156 30,148 Z" fill="{BRAND['red']}"/>
  <!-- deck pad detail — cream line along board top -->
  <line x1="60" y1="138" x2="200" y2="138" stroke="{BRAND['cream']}" stroke-width="3" stroke-linecap="round"/>
  <!-- diagonal paddle: T-grip top-right → leaning across board to lower-left, blade clearly visible -->
  <!-- shaft -->
  <line x1="225" y1="30" x2="80" y2="112" stroke="{BRAND['red']}" stroke-width="8" stroke-linecap="round"/>
  <!-- T-grip at top of paddle (perpendicular to shaft direction) -->
  <line x1="214" y1="20" x2="244" y2="38" stroke="{BRAND['red']}" stroke-width="8" stroke-linecap="round"/>
  <!-- paddle blade (rhombus oriented along paddle direction) — at bottom-left of shaft, clearly visible above board -->
  <path d="M80,112 L96,118 L80,138 L60,124 Z" fill="{BRAND['red']}"/>
</svg>''',
}


# ============================================================
# THEMES — content slots per post
# ============================================================
THEMES = {
    "M1_guide": {
        "template":    "M1",
        "issue_tag":   "GUIDE / 02",                   # right-top mono
        "kicker":      "GUIDE · 02 · 2026",            # accent eyebrow
        "hero":        "5",                            # big italic number/date
        "hero_word":   "ФИШЕК",                        # word next to hero (or None)
        "title_main":  "ДЛЯ ПЕРВОГО",                  # italic main
        "title_accent": "ЗАЕЗДА",                      # gradient accent word
        "subtitle":    "которые превратят тебя из&nbsp;новичка в&nbsp;гонщика за&nbsp;один день",
        "counter":     "01 / 07",
        "handle":      "@livedrive.tmn",
    },
    "M1_announce": {
        "template":    "M1",
        "issue_tag":   "АНОНС · 2026",
        "kicker":      "СЕЗОН · ОТКРЫТИЕ",
        "hero":        "23/04",                        # date as hero
        "hero_word":   None,                           # no word — date is everything
        "title_main":  "НАЧАЛО",
        "title_accent": "НОВОГО СЕЗОНА",
        "subtitle":    "первая гонка сезона · регистрация открыта · 20 пилотов на старте",
        "counter":     "01 / 05",
        "handle":      "@livedrive.tmn",
    },
    "M2_guide": {
        "template":    "M2",
        "issue_tag":   "GUIDE №02  ·  МАЙ 2026",
        "kicker":      "PIT-STOP / GUIDE",
        "hero":        "5",
        "hero_word":   "ФИШЕК",
        "title_main":  "ДЛЯ ПЕРВОГО",
        "title_accent": "ЗАЕЗДА",
        "subtitle":    "которые превратят тебя из&nbsp;новичка в&nbsp;гонщика за&nbsp;один день",
        "counter":     "01 / 07",
        "handle":      "@livedrive.tmn",
    },
    "M6_glamping": {
        "template":    "M6",
        "icon":        "tent",
        "issue_tag":   "OUTDOOR · #03",
        "kicker":      "GLAMPING · ВЫХОДНЫЕ · ТЮМЕНЬ",
        "hero":        "5",
        "hero_word":   "ПРИЧИН",
        "title_main":  "ВЫБРАТЬ",
        "title_accent": "НАШ ГЛЭМПИНГ",
        "subtitle":    "семейный отдых на природе · в&nbsp;часе от&nbsp;города · от&nbsp;3&nbsp;900&nbsp;₽ за&nbsp;ночь",
        "counter":     "01 / 06",
        "handle":      "@livedrive.tmn",
    },
    "M6_sup": {
        "template":    "M6",
        "icon":        "sup",
        "issue_tag":   "OUTDOOR · #04",
        "kicker":      "SUP · СПЛАВ ПО РЕКЕ · ТЮМЕНЬ",
        "hero":        "5",
        "hero_word":   "ПРИЧИН",
        "title_main":  "ВСТАТЬ НА",
        "title_accent": "САПБОРД ЛЕТОМ",
        "subtitle":    "сплав по реке группой · 1,5&nbsp;часа на&nbsp;воде · доска и&nbsp;весло в&nbsp;комплекте · от&nbsp;800&nbsp;₽",
        "counter":     "01 / 06",
        "handle":      "@livedrive.tmn",
    },
}


# ============================================================
# M1 — Anniversary Rings (продолжение шаблона 23/04)
# ============================================================
def render_m1(t: dict) -> str:
    # Размер hero подстраивается под длину (одна цифра — 520px, дата — 320px)
    is_date = len(t["hero"]) > 2
    hero_size = "320px" if is_date else "520px"
    hero_top = "300px" if is_date else "240px"
    hero_letter = "-10px" if is_date else "-22px"
    word_visible = t.get("hero_word") is not None
    css = f"""
.slide{{background:{BRAND['black']};color:{BRAND['white']};}}
/* overlapping red+orange rings — signature decor */
.rings{{position:absolute;right:-180px;top:140px;width:780px;height:780px;}}
.ring{{position:absolute;border-radius:50%;border-style:solid;}}
.ring.r1{{width:560px;height:560px;left:0;top:60px;border:34px solid {BRAND['red']};opacity:0.95;}}
.ring.r2{{width:560px;height:560px;left:200px;top:200px;border:34px solid {BRAND['orange']};opacity:0.95;mix-blend-mode:screen;}}
.ring.r3{{width:380px;height:380px;left:120px;top:0;border:22px solid {BRAND['orange']};opacity:0.75;}}
.brand{{position:absolute;top:54px;left:60px;z-index:5;}}
.tag{{position:absolute;top:64px;right:60px;font-family:{BRAND['font_mono']};font-size:14px;letter-spacing:3px;color:{BRAND['white']};font-weight:500;z-index:5;}}
.kicker{{position:absolute;left:60px;top:200px;font-family:{BRAND['font_mono']};font-size:18px;letter-spacing:5px;color:{BRAND['orange']};font-weight:700;text-transform:uppercase;z-index:5;}}
.hero{{position:absolute;left:40px;top:{hero_top};
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:{hero_size};line-height:0.85;color:{BRAND['white']};
  letter-spacing:{hero_letter};z-index:6;
  text-shadow:0 0 40px rgba(200,32,42,0.3);}}
.hero-word{{position:absolute;left:340px;top:340px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:104px;line-height:1;color:{BRAND['white']};letter-spacing:-3px;z-index:6;
  {'' if word_visible else 'display:none;'}}}
.bigtitle{{position:absolute;left:60px;top:790px;right:60px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:96px;line-height:0.95;color:{BRAND['white']};letter-spacing:-3px;z-index:6;}}
.bigtitle .accent{{
  background:linear-gradient(90deg,{BRAND['red']},{BRAND['orange']});
  -webkit-background-clip:text;background-clip:text;color:transparent;}}
.subtitle{{position:absolute;left:60px;right:380px;bottom:130px;
  font-family:{BRAND['font_body']};font-style:italic;font-weight:500;
  font-size:24px;line-height:1.35;color:rgba(255,255,255,0.75);z-index:5;}}
.footer{{position:absolute;bottom:0;left:0;right:0;height:64px;
  border-top:1px solid rgba(255,255,255,0.12);
  display:flex;align-items:center;justify-content:space-between;
  padding:0 60px;
  font-family:{BRAND['font_mono']};font-size:15px;color:rgba(255,255,255,0.7);
  letter-spacing:2px;font-weight:500;}}
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


# ============================================================
# M2 — Pit-Stop Editorial (racing-tape + angled title)
# ============================================================
def render_m2(t: dict) -> str:
    is_date = len(t["hero"]) > 2
    hero_size = "200px" if is_date else "340px"
    hero_letter = "-6px" if is_date else "-14px"
    word_visible = t.get("hero_word") is not None
    css = f"""
.slide{{background:{BRAND['black']};color:{BRAND['white']};}}
.tape{{position:absolute;left:0;right:0;top:560px;height:54px;
  background:linear-gradient(90deg,{BRAND['red']} 0%,{BRAND['orange']} 100%);z-index:2;}}
.tape2{{position:absolute;left:0;right:0;top:630px;height:6px;
  background:linear-gradient(90deg,{BRAND['orange']} 0%,{BRAND['red']} 100%);z-index:2;opacity:0.55;}}
.tape .tape-text{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:32px;
  font-family:{BRAND['font_mono']};font-size:18px;font-weight:700;
  color:{BRAND['black']};letter-spacing:6px;text-transform:uppercase;}}
.check{{position:absolute;left:0;right:0;bottom:0;height:34px;
  background:repeating-linear-gradient(90deg,{BRAND['white']} 0,{BRAND['white']} 34px,{BRAND['black']} 34px,{BRAND['black']} 68px);z-index:3;}}
.brand{{position:absolute;top:54px;left:60px;z-index:6;}}
.issue{{position:absolute;top:66px;right:60px;font-family:{BRAND['font_mono']};
  font-size:14px;letter-spacing:3px;color:{BRAND['white']};font-weight:500;z-index:6;}}
.eyebrow{{position:absolute;left:60px;top:200px;
  font-family:{BRAND['font_mono']};font-size:16px;letter-spacing:5px;
  color:{BRAND['orange']};font-weight:700;text-transform:uppercase;z-index:5;}}
.hero{{position:absolute;left:60px;top:230px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:{hero_size};line-height:0.88;letter-spacing:{hero_letter};z-index:5;
  background:linear-gradient(135deg,{BRAND['red']},{BRAND['orange']});
  -webkit-background-clip:text;background-clip:text;color:transparent;padding-right:30px;}}
.stacked{{position:absolute;left:430px;top:260px;z-index:5;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  line-height:0.95;color:{BRAND['white']};letter-spacing:-2px;
  {'' if word_visible else 'display:none;'}}}
.stacked .top{{font-size:78px;}}
.stacked .word{{font-size:120px;margin-top:6px;}}
.maintitle{{position:absolute;left:60px;right:60px;top:700px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:128px;line-height:0.92;color:{BRAND['white']};letter-spacing:-4px;z-index:4;}}
.maintitle .l2{{display:block;color:{BRAND['orange']};}}
.subtitle{{position:absolute;left:60px;right:60px;bottom:90px;
  font-family:{BRAND['font_body']};font-style:italic;font-weight:500;
  font-size:24px;line-height:1.35;color:rgba(255,255,255,0.75);z-index:5;max-width:780px;}}
.counter{{position:absolute;right:60px;top:200px;
  font-family:{BRAND['font_mono']};font-size:16px;letter-spacing:3px;
  color:rgba(255,255,255,0.6);font-weight:500;z-index:5;}}
"""
    body = f"""
<div class="brand">{ld_badge('color', 140)}</div>
<div class="issue">{t['issue_tag']}</div>
<div class="eyebrow">{t['kicker']}</div>
<div class="counter">{t['counter']}</div>
<div class="hero">{t['hero']}</div>
<div class="stacked"><div class="top">ТОП</div><div class="word">{t['hero_word'] or ''}</div></div>
<div class="tape"><div class="tape-text"><span>RACE&nbsp;READY</span><span>·</span><span>LIVE&nbsp;DRIVE</span><span>·</span><span>TYUMEN</span></div></div>
<div class="tape2"></div>
<div class="maintitle">{t['title_main']}<span class="l2">{t['title_accent']}</span></div>
<div class="subtitle">{t['subtitle']}</div>
<div class="check"></div>
"""
    return wrap(css, body)


# ============================================================
# M6 — Outdoor Quiet (sub-brand для глэмпинга/сапов/природы)
# ============================================================
def render_m6(t: dict) -> str:
    is_date = len(t["hero"]) > 2
    hero_size = "200px" if is_date else "320px"
    hero_letter = "-6px" if is_date else "-12px"
    word_visible = t.get("hero_word") is not None
    # Vertical rule text — adapts to issue type
    vrule_text = t['kicker'].replace(" · ", " · ")  # use kicker as rule label
    css = f"""
.slide{{background:{BRAND['cream']};color:{BRAND['navy']};}}
.v-rule{{position:absolute;right:120px;top:280px;width:2px;height:340px;
  background:linear-gradient({BRAND['red']},{BRAND['orange']});z-index:2;}}
.v-rule::after{{content:'{vrule_text}';
  position:absolute;right:-10px;top:50%;transform-origin:right center;
  transform:translate(100%,-50%) rotate(-90deg);
  font-family:{BRAND['font_mono']};font-size:12px;letter-spacing:4px;
  color:{BRAND['red']};font-weight:600;white-space:nowrap;}}
.brand-icon{{position:absolute;left:60px;bottom:215px;width:124px;height:96px;z-index:3;opacity:0.95;}}
.brand{{position:absolute;top:54px;left:60px;z-index:6;}}
.issue{{position:absolute;top:66px;right:60px;font-family:{BRAND['font_mono']};
  font-size:14px;letter-spacing:3px;color:{BRAND['navy']};font-weight:500;z-index:6;}}
.eyebrow{{position:absolute;left:60px;top:230px;
  font-family:{BRAND['font_mono']};font-size:15px;letter-spacing:5px;
  color:{BRAND['red']};font-weight:700;text-transform:uppercase;z-index:5;
  max-width:520px;}}
.hero{{position:absolute;left:60px;top:280px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:{hero_size};line-height:0.9;color:{BRAND['navy']};letter-spacing:{hero_letter};z-index:5;}}
.hero .uline{{position:relative;display:inline-block;
  background-image:linear-gradient(90deg,{BRAND['red']},{BRAND['orange']});
  background-size:100% 18px;
  background-position:0 calc(100% - 24px);
  background-repeat:no-repeat;padding:0 0.04em;}}
.t-top{{position:absolute;left:340px;top:300px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:64px;color:{BRAND['navy']};letter-spacing:-2px;z-index:5;
  {'' if word_visible else 'display:none;'}}}
.t-word{{position:absolute;left:340px;top:380px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:88px;color:{BRAND['navy']};letter-spacing:-2px;z-index:5;
  {'' if word_visible else 'display:none;'}}}
.rule{{position:absolute;left:60px;right:60px;top:710px;height:1px;background:{BRAND['navy']};opacity:0.2;z-index:4;}}
.maintitle{{position:absolute;left:60px;right:60px;top:760px;
  font-family:{BRAND['font_title']};font-style:italic;font-weight:900;
  font-size:88px;line-height:0.94;color:{BRAND['navy']};letter-spacing:-3px;z-index:5;}}
.maintitle .accent{{color:{BRAND['red']};}}
.subtitle{{position:absolute;left:60px;right:60px;bottom:140px;
  font-family:{BRAND['font_body']};font-style:italic;font-weight:500;
  font-size:24px;line-height:1.4;color:{BRAND['navy']};opacity:0.75;z-index:5;max-width:780px;}}
.footer{{position:absolute;bottom:0;left:0;right:0;height:64px;
  border-top:1px solid rgba(26,34,56,0.15);
  display:flex;align-items:center;justify-content:space-between;
  padding:0 60px;
  font-family:{BRAND['font_mono']};font-size:15px;color:{BRAND['navy']};
  letter-spacing:2px;font-weight:500;}}
.footer .dot{{width:8px;height:8px;background:{BRAND['red']};display:inline-block;margin-right:10px;vertical-align:middle;}}
"""
    # Brand icon — выбирается по slot t["icon"] из ICONS dict (см. ниже)
    # 10 мая 2026: заменил flame-icon на параметризованную систему — tent / sup / ...
    icon_slug = t.get("icon", "tent")
    icon_svg = ICONS.get(icon_slug, ICONS["tent"])
    body = f"""
<div class="v-rule"></div>
<div class="brand">{ld_badge('black', 140)}</div>
<div class="issue">{t['issue_tag']}</div>
<div class="eyebrow">{t['kicker']}</div>
<div class="hero"><span class="uline">{t['hero']}</span></div>
<div class="t-top">ТОП</div>
<div class="t-word">{t['hero_word'] or ''}</div>
<div class="rule"></div>
<div class="maintitle">{t['title_main']}<br><span class="accent">{t['title_accent']}</span></div>
<div class="brand-icon">{icon_svg}</div>
<div class="subtitle">{t['subtitle']}</div>
<div class="footer">
  <span><span class="dot"></span>{t['handle']}</span>
  <span>{t['counter']}</span>
</div>
"""
    return wrap(css, body)


# ============================================================
# Dispatch
# ============================================================
TEMPLATE_RENDERERS = {
    "M1": render_m1,
    "M2": render_m2,
    "M6": render_m6,
}


def main():
    for slug, theme in THEMES.items():
        renderer = TEMPLATE_RENDERERS[theme["template"]]
        html = renderer(theme)
        (HTML_DIR / f"{slug}.html").write_text(html, encoding="utf-8")
        print(f"HTML  {slug}.html  ({theme['template']})")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for slug in THEMES.keys():
            page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
            page.goto((HTML_DIR / f"{slug}.html").as_uri())
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(1500)
            page.screenshot(
                path=str(PNG_DIR / f"{slug}.png"),
                clip={"x": 0, "y": 0, "width": 1080, "height": 1350},
            )
            page.close()
            print(f"PNG   {slug}.png")
        browser.close()
    print(f"\nDONE — {len(THEMES)} PNG в {PNG_DIR}")


if __name__ == "__main__":
    main()
