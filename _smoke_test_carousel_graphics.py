"""Smoke-тест графсистемы карусели — типы inner-тайла A/B/C.

Рендерит карусель cover(M2) + 3 inner-слайда (slide_type A/B/C) через
carousel.renderer.render_carousel, проверяет PNG и оставляет их для
визуального аудита.

Запуск:  python _smoke_test_carousel_graphics.py
Выход:   _carousel_graphics_smoke/slide_01..04.png
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from carousel.renderer import render_carousel

OUT = Path(__file__).parent / "_carousel_graphics_smoke"

COVER = {
    "template": "M2",
    "issue_tag": "GUIDE №03 · МАЙ",
    "kicker": "GUIDE · ДЕЛЕГИРОВАНИЕ",
    "hero": "3",
    "hero_word": "ПРАВИЛА",
    "title_main": "БЕЗ ПОТЕРИ",
    "title_accent": "КОНТРОЛЯ",
    "subtitle": "как отдать операционку и не потерять качество — из практики Life Drive",
    "counter": "01 / 04",
    "handle": "@livedrive.tmn",
}

INNER_A = {
    "slide_type": "A",
    "kicker": "Тезис",
    "title": "Ты — потолок своего бизнеса",
    "accent_word": "потолок",
    "body": "Пока каждое решение проходит через тебя, компания растёт со скоростью одного человека.",
    "counter": "02 / 04",
    "handle": "@livedrive.tmn",
}

INNER_B = {
    "slide_type": "B",
    "kicker": "Что делать",
    "title": "Передавай результат, а не инструкцию",
    "accent_word": "результат",
    "body": "Назови, каким должен быть итог и срок. Как его достичь — зона ответственности сотрудника.",
    "counter": "03 / 04",
    "handle": "@livedrive.tmn",
}

INNER_C = {
    "slide_type": "C",
    "kicker": "Главный принцип",
    "pull_quote": "Бизнес растёт ровно настолько, насколько ты готов отпустить штурвал.",
    "accent_word": "отпустить штурвал",
    "counter": "04 / 04",
    "handle": "@livedrive.tmn",
}


def _resolution(path: Path) -> tuple[int, int]:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    w, h = res.stdout.strip().split(",")[:2]
    return int(w), int(h)


def main() -> int:
    slides = [COVER, INNER_A, INNER_B, INNER_C]
    pngs = render_carousel(slides, OUT)

    ok = True
    if len(pngs) != 4:
        print(f"FAIL: ожидалось 4 PNG, отрендерено {len(pngs)}")
        return 1
    for p in pngs:
        if not p.exists() or p.stat().st_size < 10_000:
            print(f"FAIL: пустой/отсутствует {p}")
            ok = False
            continue
        w, h = _resolution(p)
        if (w, h) != (2160, 2700):
            print(f"FAIL: {p.name} разрешение {w}×{h}, ожидалось 2160×2700")
            ok = False
        else:
            print(f"OK: {p.name} — {w}×{h}, {p.stat().st_size // 1024} KB")

    print("\n" + ("✅ SMOKE PASS" if ok else "❌ SMOKE FAIL"))
    print(f"PNG для аудита: {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
