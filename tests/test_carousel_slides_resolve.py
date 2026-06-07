"""Тест _resolve_carousel_slides (11 июня).

Карусель из ТЕМЫ (cmd_carousel) не имеет seed_card_id → раньше publish не
находил слайды. Теперь render пишет пути PNG в draft, а publish читает их
через _resolve_carousel_slides (фолбэк — по карточке).

Запуск: python tests/test_carousel_slides_resolve.py
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

sys.path.insert(0, str(Path(__file__).parent.parent))

import carousel.handlers as ch  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    tmp = Path(tempfile.mkdtemp(prefix="cslides_"))
    p1 = tmp / "slide_01.png"; p1.write_bytes(b"x")
    p2 = tmp / "slide_02.png"; p2.write_bytes(b"x")
    p3 = tmp / "slide_03.png"; p3.write_bytes(b"x")

    print("\n[_resolve_carousel_slides — draft-пути (карусель из темы)]")
    draft = {"carousel_png_paths": [str(p3), str(p1), str(p2)]}  # неотсортированы
    res = ch._resolve_carousel_slides(draft, seed_card_id=None)
    _assert(res == [str(p1), str(p2), str(p3)],
            f"вернул отсортированные 3 слайда из draft (без seed), got {res}", errors)

    print("\n[фильтр несуществующих]")
    draft2 = {"carousel_png_paths": [str(p1), str(tmp / "nope.png")]}  # только 1 существует
    res2 = ch._resolve_carousel_slides(draft2, seed_card_id=None)
    _assert(res2 == [], f"<2 существующих + нет seed → [], got {res2}", errors)

    print("\n[пустой draft, нет seed → []]")
    _assert(ch._resolve_carousel_slides({}, None) == [], "пусто → []", errors)

    print("\n[фолбэк по карточке (seed) если draft пуст]")
    # monkeypatch project_dir → temp проект с carousel/slide_*.png
    proj = Path(tempfile.mkdtemp(prefix="cproj_"))
    cdir = proj / "carousel"; cdir.mkdir()
    (cdir / "slide_01.png").write_bytes(b"x")
    (cdir / "slide_02.png").write_bytes(b"x")
    import bot_state
    orig = bot_state.project_dir
    bot_state.project_dir = lambda d: proj
    try:
        res3 = ch._resolve_carousel_slides({}, seed_card_id="abc123def")
    finally:
        bot_state.project_dir = orig
    _assert(len(res3) == 2 and all("slide_" in p for p in res3),
            f"фолбэк по seed нашёл 2 слайда в проекте, got {res3}", errors)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
