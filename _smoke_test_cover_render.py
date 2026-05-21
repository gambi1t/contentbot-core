"""Smoke-тест рендера обложки pipeline #3 — generate_cover с фото Максима.

Рендерит 2 обложки: фото Максима (фон) + текст-плашка. Тексты — из 5
вариантов, что выдал cover-промпт. Проверяем глазами вёрстку и лицо Максима.

Запуск:  python _smoke_test_cover_render.py
Выход:   _cover_render_smoke/cover_1.png  cover_2.png
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "_cover_render_smoke"

CASES = [
    ("assets/avatars/maksim/max_2.JPG",
     "Голова собственника не для операционки", "cover_1.png"),
    ("assets/avatars/maksim/max_1.JPG",
     "Я больше не ставлю задачи руками", "cover_2.png"),
]


def main() -> int:
    OUT.mkdir(exist_ok=True)
    print("импортирую bot.py (может занять несколько секунд)…")
    from bot import generate_cover

    for avatar, text, out_name in CASES:
        avatar_path = BASE / avatar
        if not avatar_path.exists():
            print(f"FAIL: нет фото {avatar_path}")
            return 1
        out_path = OUT / out_name
        generate_cover(text, str(out_path), avatar_override=str(avatar_path))
        size_kb = out_path.stat().st_size // 1024 if out_path.exists() else 0
        print(f"OK: {out_name} — текст «{text}» ({size_kb} KB)")

    print(f"\nготово → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
