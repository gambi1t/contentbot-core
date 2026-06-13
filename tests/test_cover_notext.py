"""Тест обложки «без текста» (13 июня).

Артём: в выборе текста обложки нет кнопки «без текста» — нужна во всех
пайплайнах. Ядро: generate_cover с пустым текстом отдаёт само фото (без
плашки/текста), не падает на textwrap("")/bbox-пустой-строки.

Запуск: python tests/test_cover_notext.py
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

from PIL import Image  # noqa: E402
import bot  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    work = Path(tempfile.mkdtemp(prefix="cover_notext_"))
    # фото-основа (как выбранное фото обложки)
    base = work / "photo.jpg"
    Image.new("RGB", (1080, 1920), (30, 60, 120)).save(base, "JPEG")

    print("\n[generate_cover('') — фото без текста, не падает]")
    out1 = str(work / "cover_empty.jpg")
    res = bot.generate_cover("", out1, avatar_override=str(base))
    _assert(res == out1 and Path(out1).exists(), "пустой текст → файл создан", errors)
    img = Image.open(out1)
    _assert(img.size[0] > 0 and img.size[1] > 0, "валидное изображение", errors)

    print("\n[пробелы — тоже без текста]")
    out2 = str(work / "cover_ws.jpg")
    bot.generate_cover("   ", out2, avatar_override=str(base))
    _assert(Path(out2).exists(), "пробельный текст → файл создан", errors)

    print("\n[без текста ≠ обычная обложка (нет градиентного затемнения низа)]")
    # У обычной обложки низ затемнён градиентом под текст. У «без текста»
    # градиента нет → средняя яркость низа ≈ верх (для ровного фото).
    import numpy as np  # локально для теста
    notext = np.asarray(Image.open(out1).convert("RGB")).astype(float)
    h = notext.shape[0]
    top_mean = notext[: int(h * 0.2), :, :].mean()
    bottom_mean = notext[int(h * 0.8):, :, :].mean()
    _assert(abs(top_mean - bottom_mean) < 10,
            f"низ не затемнён градиентом (Δяркости {abs(top_mean-bottom_mean):.1f})", errors)

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
