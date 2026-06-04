"""TDD: whitelist CDN-доменов в `_scene_valid_minimal` (4 июня, по итогу прогона).

История: 4 июня прогон build-фазы scene_01 — Claude за 6 мин записал валидный
HTML (turns=4, Write=1), но мой `_scene_valid_minimal` его завернул из-за
`https://cdn.jsdelivr.net/npm/gsap`. GSAP — стандартная библиотека HF (фреймворк
именно на ней построен). `npx hyperframes render` сам ходит за ней. Это НЕ
«ассет», а движочная зависимость — её надо разрешать.

Контракт:
  Разрешённые домены (CDN библиотек, ходим программно при рендере):
    cdn.jsdelivr.net, fonts.googleapis.com, fonts.gstatic.com, unpkg.com
  Плюс уже разрешённый w3.org (SVG xmlns).
  Произвольные `https://example.com/image.png` — по-прежнему ОТВЕРГАЕМ.

Run: python tests/test_scene_valid_url_whitelist.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))
import hyperframes_broll as H  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _scene_html(extra: str = "") -> str:
    """Минимальный валидный каркас сцены + extra (для теста URL)."""
    return ('<!doctype html><html><head>' + extra + '</head><body>'
            '<div data-composition-id="scene_01" data-duration="5" '
            'data-width="1080" data-height="1920"></div>'
            '<script>window.__timelines={};const tl=gsap.timeline({paused:true});</script>'
            '</body></html>' + "x" * 5000)


def _write(d: Path, name: str, content: str) -> Path:
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


def test_gsap_cdn_allowed(errors):
    print("\n-- GSAP CDN (jsdelivr) — разрешён (зависимость движка) --")
    d = Path(tempfile.mkdtemp())
    html = _scene_html('<script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>')
    p = _write(d, "gsap.html", html)
    ok, issues = H._scene_valid_minimal(p, "scene_01")
    _assert(ok, f"gsap CDN не должен валить (issues={issues})", errors)


def test_google_fonts_allowed(errors):
    print("\n-- Google Fonts (googleapis + gstatic) — разрешён --")
    d = Path(tempfile.mkdtemp())
    html = _scene_html(
        '<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">'
        '<style>@font-face{src:url(https://fonts.gstatic.com/s/inter/v12/x.woff2);}</style>'
    )
    p = _write(d, "fonts.html", html)
    ok, issues = H._scene_valid_minimal(p, "scene_01")
    _assert(ok, f"Google Fonts не должен валить (issues={issues})", errors)


def test_unpkg_allowed(errors):
    print("\n-- unpkg.com (npm CDN) — разрешён --")
    d = Path(tempfile.mkdtemp())
    html = _scene_html('<script src="https://unpkg.com/three@0.150.0/build/three.min.js"></script>')
    p = _write(d, "unpkg.html", html)
    ok, issues = H._scene_valid_minimal(p, "scene_01")
    _assert(ok, f"unpkg не должен валить (issues={issues})", errors)


def test_arbitrary_image_url_rejected(errors):
    print("\n-- произвольный https image URL — ОТВЕРГАЕМ (рендер оффлайн) --")
    d = Path(tempfile.mkdtemp())
    html = _scene_html('<img src="https://example.com/random/picture.jpg">')
    p = _write(d, "img.html", html)
    ok, issues = H._scene_valid_minimal(p, "scene_01")
    _assert(not ok, "произвольный URL должен валить", errors)
    _assert(any("http" in i.lower() or "url" in i.lower() for i in issues),
            f"issue про URL (got {issues})", errors)


def test_typosquat_rejected(errors):
    print("\n-- typosquat (cdn.jsdelivr.net.attacker.com) — ОТВЕРГАЕМ --")
    d = Path(tempfile.mkdtemp())
    html = _scene_html('<script src="https://cdn.jsdelivr.net.attacker.com/gsap.js"></script>')
    p = _write(d, "ts.html", html)
    ok, issues = H._scene_valid_minimal(p, "scene_01")
    _assert(not ok, "тайпосквот должен валить (whitelist строго по домену)", errors)


def main():
    print("=" * 60)
    print("test_scene_valid_url_whitelist")
    print("=" * 60)
    errors = []
    test_gsap_cdn_allowed(errors)
    test_google_fonts_allowed(errors)
    test_unpkg_allowed(errors)
    test_arbitrary_image_url_rejected(errors)
    test_typosquat_rejected(errors)
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
