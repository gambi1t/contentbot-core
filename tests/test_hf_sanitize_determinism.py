"""TDD: авто-санитайзер детерминизма HF (repeat:-1 → repeat:0).

Реальный сбой 21 июня: panferov scene_06 не собрался за 3 попытки —
модель ставила `repeat:-1` (бесконечный GSAP-цикл), `_scene_valid_minimal`
это реджектит (frame-by-frame рендер требует конечной анимации). Санитайзер
чинит детерминированно. Math.random/Date.now НЕ авто-чиним (нельзя безопасно).

Run: python tests/test_hf_sanitize_determinism.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import hyperframes_broll as h  # noqa: E402

_errs: list[str] = []


def _assert(cond, msg):
    print(f"  {'OK' if cond else 'X FAIL'} {msg}")
    if not cond:
        _errs.append(msg)


def _scene_html(extra_js: str) -> str:
    body = "<div>контент сцены безопасный текст</div>\n" * 300  # padding >5000
    return f"""<!doctype html><html><head><meta charset="utf-8"></head>
<body>
<div id="scene_06" data-composition-id="scene_06" data-width="1080" data-height="1920" data-duration="5">
{body}
</div>
<script>
const tl = gsap.timeline({{paused:true}});
{extra_js}
window.__timelines = window.__timelines || {{}};
window.__timelines["scene_06"] = tl;
</script></body></html>"""


def test_sanitize_variants():
    print("\n-- repeat:-1 варианты → repeat:0 --")
    for src in ["repeat:-1", "repeat: -1", "repeat : -1", "repeat:  -1"]:
        out = h._sanitize_scene_html(f"gsap.to(x,{{{src},yoyo:true}})")
        _assert("repeat:0" in out and "-1" not in out, f"{src!r} → {out!r}")


def test_finite_repeat_untouched():
    print("\n-- конечный repeat НЕ трогаем --")
    out = h._sanitize_scene_html("gsap.to(x,{repeat:3,yoyo:true})")
    _assert("repeat:3" in out, "repeat:3 сохранён")


def test_validator_passes_after_sanitize():
    print("\n-- scene с repeat:-1: невалидна ДО, валидна ПОСЛЕ --")
    bad = _scene_html("tl.to('#scene_06',{scale:1.1,repeat:-1,yoyo:true,duration:0.5});")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "scene_06.html"
        p.write_text(bad, encoding="utf-8")
        _ok, iss = h._scene_valid_minimal(p, "scene_06")
        _assert(any("repeat:-1" in i for i in iss), f"ДО: repeat-issue присутствует ({iss})")
        p.write_text(h._sanitize_scene_html(bad), encoding="utf-8")
        ok2, iss2 = h._scene_valid_minimal(p, "scene_06")
        _assert(not any("repeat:-1" in i for i in iss2), f"ПОСЛЕ: repeat-issue нет ({iss2})")
        _assert(ok2, f"ПОСЛЕ: сцена валидна целиком ({iss2})")


def test_random_clock_untouched():
    print("\n-- Math.random/Date.now НЕ авто-чиним --")
    out = h._sanitize_scene_html("var a=Math.random(); var b=Date.now();")
    _assert("Math.random" in out and "Date.now" in out, "random/clock не тронуты")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"{'='*60}\nRunning {len(tests)} HF sanitize tests\n{'='*60}")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _errs.append(f"{fn.__name__}: {e}")
            print(f"  X EXC {fn.__name__}: {e}")
    print(f"\n{'='*60}")
    print("ALL PASS" if not _errs else f"FAIL ({len(_errs)}): " + "; ".join(_errs))
    sys.exit(0 if not _errs else 1)
