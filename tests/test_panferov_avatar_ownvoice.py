"""Парити: «свой голос» (HeyGen-аватар) у panferov.

24 июня 2026: «свой голос» (запись → говорит аватар, callback heygen_selfvoice)
показывался кнопкой ТОЛЬКО в бренд-ветке maksim пикера версий аватара
(bot.py ~19022). Хендлеры heygen_selfvoice/awaiting_selfvoice НЕ гейтнуты —
перекос чисто в UI. Артём: пайплайны panferov = Максима, разница только в стиле
→ кнопка должна быть и у него. Тест проверяет, что в panferov-ветке пикера
(её уникальный якорь — "Avatar 3 — мягкое движение, дешевле") присутствует
heygen_selfvoice.

Стиль: без pytest, main() → 0/1.
Запуск: python tests/test_panferov_avatar_ownvoice.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe_msg = msg.encode("ascii", "replace").decode("ascii")
    if not cond:
        errors.append(f"FAIL {safe_msg}")
        print(f"  FAIL {safe_msg}")
    else:
        print(f"  OK {safe_msg}")


# Якорь panferov-ветки else пикера версий (есть ТОЛЬКО там, не в maksim-ветке).
_PANFEROV_PICKER_ANCHOR = "Avatar 3 — мягкое движение, дешевле"


def test_panferov_picker_has_selfvoice(errors: list[str]) -> None:
    print("\n-- panferov-ветка пикера версий аватара содержит heygen_selfvoice --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    idx = src.find(_PANFEROV_PICKER_ANCHOR)
    _assert(idx != -1, "найден panferov-пикер версий аватара (якорь)", errors)
    if idx == -1:
        return
    # окно = else-ветка пикера: от якоря до её edit_message_text (НЕ дальше,
    # иначе захватит секцию хендлера heygen_selfvoice за пикером — ложный зелёный)
    end = src.find("edit_message_text", idx)
    window = src[idx: end if end != -1 else idx + 600]
    _assert("heygen_selfvoice" in window,
            "кнопка «свой голос» (callback heygen_selfvoice) в panferov-ветке", errors)


def test_selfvoice_button_in_both_brand_branches(errors: list[str]) -> None:
    print("\n-- кнопка-вход heygen_selfvoice есть в обеих ветках пикера (maksim+panferov) --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    n = src.count('callback_data="heygen_selfvoice")')  # без :v3/:v4 — это вход
    _assert(n >= 2, f"вход own-voice в обоих брендах пикера (got {n})", errors)


def main() -> int:
    errors: list[str] = []
    for fn in (test_panferov_picker_has_selfvoice,
               test_selfvoice_button_in_both_brand_branches):
        fn(errors)
    print("\n" + ("FAIL" if errors else "OK") + f" ({len(errors)} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
