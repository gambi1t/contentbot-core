"""_proto_typography.py — прототип графической системы inner-слайдов карусели.

Standalone-демо: рендерит 3 примера inner-слайдов карусели maksim-bot,
демонстрируя типографику-first графсистему БЕЗ фото и внешних зависимостей.

3 типа inner-тайла:
  A — Statement  — тезис: гигантская типографика + ghost-цифра
  B — Breakdown  — пункт списка: крупная индекс-цифра + speed-rails
  C — Quote      — цитата/вывод: pull-quote с красной линейкой

Графприёмы: ghost-цифра, speed-rails, шахматный финиш-мотив,
pull-quote блок, telemetry-strip прогресса серии.

НЕ интегрирован в бота — это прототип на одобрение. Рендер локальный.

Запуск:  python _proto_typography.py
Выход:   _proto_typography/slide_A.png  slide_B.png  slide_C.png
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

# ============================================================
# BRAND TOKENS — зеркало carousel/renderer.py BRAND
# ============================================================
RED = "#C8202A"
ORANGE = "#F26622"
BLACK = "#0A0A0A"
WHITE = "#FFFFFF"
GREY = "#2A2A2A"
F_TITLE = "'Inter Tight', sans-serif"
F_BODY = "'Inter', sans-serif"
F_MONO = "'JetBrains Mono', monospace"
HANDLE = "@livedrive.tmn"

GOOGLE_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    "family=Inter:ital,wght@0,400;0,500;0,600;0,800;0,900;1,500;1,800;1,900"
    "&family=Inter+Tight:ital,wght@0,800;0,900;1,800;1,900"
    '&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">'
)

BASE_RESET = (
    "*{box-sizing:border-box;margin:0;padding:0;"
    "-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;}"
    "html,body{width:1080px;height:1350px;overflow:hidden;}"
    ".slide{width:1080px;height:1350px;position:relative;overflow:hidden;"
    f"background:{BLACK};color:{WHITE};font-family:{F_BODY};}}"
)

# ============================================================
# Общие графические элементы (CSS — один блок на все слайды)
# ============================================================
SHARED_CSS = f"""
/* --- LD badge --- */
.brand{{position:absolute;top:54px;left:60px;z-index:6;}}
.tag{{position:absolute;top:66px;right:60px;font-family:{F_MONO};font-size:14px;
  letter-spacing:3px;color:rgba(255,255,255,0.7);font-weight:500;z-index:6;}}

/* --- ghost-цифра: огромный номер слайда за краем кадра --- */
.ghost{{position:absolute;font-family:{F_TITLE};font-style:italic;
  font-weight:900;color:{WHITE};line-height:0.7;z-index:0;
  user-select:none;letter-spacing:-0.04em;}}

/* --- speed-rails: диагональные racing-линии --- */
.rails{{position:absolute;height:200px;transform-origin:left center;
  z-index:1;pointer-events:none;}}
.rail{{position:absolute;left:0;border-radius:5px;}}
.rail.r1{{top:0;height:15px;width:100%;
  background:linear-gradient(90deg,rgba(200,32,42,0) 0%,{RED} 40%,{RED} 100%);}}
.rail.r2{{top:52px;height:8px;width:84%;
  background:repeating-linear-gradient(90deg,{ORANGE} 0 32px,
  rgba(242,102,34,0) 32px 56px);}}
.rail.r3{{top:88px;height:5px;width:66%;
  background:linear-gradient(90deg,rgba(255,255,255,0) 0%,
  rgba(255,255,255,0.45) 55%,rgba(255,255,255,0.45) 100%);}}

/* --- шахматный финиш-мотив --- */
.checker{{background-color:{BLACK};
  background-image:
    linear-gradient(45deg,{WHITE} 25%,transparent 25% 75%,{WHITE} 75%),
    linear-gradient(45deg,{WHITE} 25%,transparent 25% 75%,{WHITE} 75%);
  background-size:24px 24px;background-position:0 0,12px 12px;}}

/* --- telemetry-strip: прогресс серии --- */
.telemetry{{position:absolute;left:0;right:0;bottom:64px;height:6px;
  display:flex;gap:4px;z-index:6;}}
.seg{{flex:1;height:100%;}}
.seg.passed{{background:{RED};}}
.seg.current{{background:{ORANGE};}}
.seg.future{{background:{GREY};}}

/* --- footer --- */
.footer{{position:absolute;bottom:0;left:0;right:0;height:64px;
  border-top:1px solid rgba(255,255,255,0.12);display:flex;
  align-items:center;justify-content:space-between;padding:0 60px;
  font-family:{F_MONO};font-size:15px;color:rgba(255,255,255,0.85);
  letter-spacing:2px;font-weight:500;z-index:6;}}
.footer .dot{{width:8px;height:8px;background:{ORANGE};display:inline-block;
  margin-right:10px;vertical-align:middle;}}

/* --- eyebrow/kicker --- */
.eyebrow{{font-family:{F_MONO};font-size:18px;letter-spacing:5px;
  color:{ORANGE};font-weight:700;text-transform:uppercase;}}
"""


def ld_badge(w: int = 140) -> str:
    """LIVE DRIVE wordmark — flame-shape, red→orange gradient."""
    h = int(w * 0.35)
    return f'''
<svg width="{w}" height="{h}" viewBox="0 0 240 84" xmlns="http://www.w3.org/2000/svg" style="display:block">
  <defs>
    <linearGradient id="ldGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{RED}"/>
      <stop offset="100%" stop-color="{ORANGE}"/>
    </linearGradient>
  </defs>
  <path d="M8,12 Q8,4 18,4 L208,4 Q220,4 224,14 Q234,28 230,42 Q234,56 220,72 Q214,80 204,80 L18,80 Q8,80 8,72 Z" fill="url(#ldGrad)"/>
  <text x="22" y="54" font-family="Inter Tight, Inter, sans-serif" font-weight="900" font-style="italic" font-size="34" fill="{WHITE}" letter-spacing="-1">LIVE DRIVE</text>
</svg>'''


def telemetry(current: int, total: int = 7) -> str:
    """Полоса прогресса серии — 7 сегментов."""
    segs = []
    for i in range(total):
        cls = "passed" if i < current else "current" if i == current else "future"
        segs.append(f'<div class="seg {cls}"></div>')
    return f'<div class="telemetry">{"".join(segs)}</div>'


def footer(counter: str) -> str:
    return (
        f'<div class="footer"><span><span class="dot"></span>{HANDLE}</span>'
        f"<span>{counter}</span></div>"
    )


def wrap(slide_css: str, body: str) -> str:
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"{GOOGLE_FONTS}<style>{BASE_RESET}{SHARED_CSS}{slide_css}</style>"
        f'</head><body><div class="slide">{body}</div></body></html>'
    )


# ============================================================
# ТИП A — Statement (тезис)
# ============================================================
def slide_a() -> str:
    css = f"""
.ghost{{font-size:680px;right:-95px;bottom:-165px;opacity:0.055;}}
.rails{{top:632px;left:-170px;width:1460px;opacity:0.8;
  transform:rotate(-24deg);}}
.a-checker{{position:absolute;left:60px;top:214px;width:96px;height:22px;
  z-index:5;}}
.a-eyebrow{{position:absolute;left:60px;top:262px;z-index:5;}}
.a-title{{position:absolute;left:58px;right:80px;top:344px;
  font-family:{F_TITLE};font-style:italic;font-weight:900;font-size:124px;
  line-height:0.93;letter-spacing:-2px;z-index:5;
  text-shadow:0 0 48px rgba(200,32,42,0.28);}}
.a-title .accent{{color:{ORANGE};}}
.a-body{{position:absolute;left:62px;right:150px;top:812px;
  font-family:{F_BODY};font-style:italic;font-weight:500;font-size:35px;
  line-height:1.42;color:rgba(255,255,255,0.78);z-index:5;}}
.a-body .lead{{display:block;width:54px;height:5px;background:{RED};
  margin-bottom:26px;}}
"""
    body = f"""
<div class="ghost">02</div>
<div class="rails"><div class="rail r1"></div><div class="rail r2"></div>
  <div class="rail r3"></div></div>
<div class="brand">{ld_badge(140)}</div>
<div class="tag">LD · SERIES 07</div>
<div class="checker a-checker"></div>
<div class="eyebrow a-eyebrow">Тезис</div>
<div class="a-title">Ты&nbsp;&mdash; <span class="accent">потолок</span><br>своего бизнеса</div>
<div class="a-body"><span class="lead"></span>Пока каждое решение проходит через
  тебя, компания растёт со скоростью одного человека&nbsp;&mdash; твоей.</div>
{telemetry(1)}
{footer("02 / 07")}
"""
    return wrap(css, body)


# ============================================================
# ТИП B — Breakdown (пункт списка)
# ============================================================
def slide_b() -> str:
    css = f"""
.ghost{{font-size:600px;right:-50px;top:-195px;opacity:0.05;}}
.b-index{{position:absolute;left:52px;top:404px;
  font-family:{F_TITLE};font-style:italic;font-weight:900;font-size:320px;
  line-height:0.78;letter-spacing:-9px;z-index:4;
  background:linear-gradient(150deg,{RED},{ORANGE});
  -webkit-background-clip:text;background-clip:text;color:transparent;}}
.b-kicker{{position:absolute;left:462px;top:312px;z-index:5;}}
.b-title{{position:absolute;left:460px;right:74px;top:356px;
  font-family:{F_TITLE};font-style:italic;font-weight:900;font-size:80px;
  line-height:0.98;letter-spacing:-1px;z-index:5;}}
.b-title .accent{{color:{ORANGE};}}
.b-body{{position:absolute;left:462px;right:88px;top:760px;
  font-family:{F_BODY};font-style:italic;font-weight:500;font-size:32px;
  line-height:1.44;color:rgba(255,255,255,0.78);z-index:5;}}
.b-divider{{position:absolute;left:462px;top:712px;width:120px;height:5px;
  background:{RED};z-index:5;}}
"""
    body = f"""
<div class="ghost">03</div>
<div class="brand">{ld_badge(140)}</div>
<div class="tag">LD · SERIES 07</div>
<div class="b-index">01</div>
<div class="eyebrow b-kicker">Что делать</div>
<div class="b-title">Передавай <span class="accent">результат</span>,<br>а не инструкцию</div>
<div class="b-divider"></div>
<div class="b-body">Назови, каким должен быть итог и срок. Как его достичь&nbsp;&mdash;
  зона ответственности сотрудника, а не твоя забота.</div>
{telemetry(2)}
{footer("03 / 07")}
"""
    return wrap(css, body)


# ============================================================
# ТИП C — Quote / Insight (цитата-вывод)
# ============================================================
def slide_c() -> str:
    css = f"""
.ghost{{font-size:680px;left:-110px;bottom:-175px;opacity:0.05;}}
.rails{{top:250px;left:560px;width:760px;opacity:0.65;
  transform:rotate(-24deg);}}
.c-checker{{position:absolute;left:150px;top:360px;width:96px;height:22px;
  z-index:5;}}
.c-bar{{position:absolute;left:90px;top:452px;width:9px;height:486px;
  background:linear-gradient(180deg,{RED},{ORANGE});z-index:5;}}
.c-quote{{position:absolute;left:150px;right:108px;top:446px;
  font-family:{F_TITLE};font-style:italic;font-weight:800;font-size:70px;
  line-height:1.16;letter-spacing:-1px;z-index:5;}}
.c-quote .accent{{color:{ORANGE};}}
.c-attr{{position:absolute;left:150px;top:986px;font-family:{F_MONO};
  font-size:17px;letter-spacing:3px;color:rgba(255,255,255,0.5);
  font-weight:500;z-index:5;}}
.c-attr b{{color:{ORANGE};font-weight:700;}}
"""
    body = f"""
<div class="ghost">04</div>
<div class="rails"><div class="rail r1"></div><div class="rail r2"></div>
  <div class="rail r3"></div></div>
<div class="brand">{ld_badge(140)}</div>
<div class="tag">LD · SERIES 07</div>
<div class="checker c-checker"></div>
<div class="c-bar"></div>
<div class="c-quote">Бизнес растёт ровно настолько, насколько ты готов
  <span class="accent">отпустить штурвал</span>.</div>
<div class="c-attr"><b>//</b>&nbsp;&nbsp;Главный принцип серии</div>
{telemetry(3)}
{footer("04 / 07")}
"""
    return wrap(css, body)


# ============================================================
# Рендер
# ============================================================
def main() -> None:
    out = Path(__file__).parent / "_proto_typography"
    out.mkdir(exist_ok=True)
    html_dir = out / "_html"
    html_dir.mkdir(exist_ok=True)

    slides = {"A": slide_a(), "B": slide_b(), "C": slide_c()}

    for name, html in slides.items():
        (html_dir / f"slide_{name}.html").write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for name in slides:
                page = browser.new_page(
                    viewport={"width": 1080, "height": 1350},
                    device_scale_factor=2,
                )
                page.goto((html_dir / f"slide_{name}.html").as_uri())
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(1500)
                png = out / f"slide_{name}.png"
                page.screenshot(
                    path=str(png),
                    clip={"x": 0, "y": 0, "width": 1080, "height": 1350},
                )
                page.close()
                print(f"rendered {png}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
