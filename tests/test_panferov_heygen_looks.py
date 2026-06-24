"""Тест реестра HeyGen-аватаров panferov (HEYGEN_LOOKS).

24 июня 2026: Артём добавил 2 новых аватара для тестов —
"Стена с улыбкой" / "Улица с улыбкой". panferov работает на бренде `default`
(нет своего heygen_looks) → видит глобальный HEYGEN_LOOKS. Максим перекрыт
собственным BRANDS["maksim"]["heygen_looks"] → новые аватары не увидит.

Стиль: без pytest, main() → 0/1 (как test_carousel_surgical_helpers.py).
Запуск: python tests/test_panferov_heygen_looks.py
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


SMILE_AVATARS = {
    "2cca63dbc8e0440cb8c875f6b852eff3": "Стена с улыбкой",
    "c60d690f95ca41d18f0bb0284dc4c9fe": "Улица с улыбкой",
}


def test_smile_avatars_present(errors: list[str]) -> None:
    print("\n-- HEYGEN_LOOKS: оба новых аватара с верными именами --")
    by_id = {e["id"]: e["name"] for e in bot.HEYGEN_LOOKS.values()}
    for av_id, name in SMILE_AVATARS.items():
        _assert(av_id in by_id, f"id {av_id[:8]}… есть в HEYGEN_LOOKS", errors)
        _assert(by_id.get(av_id) == name, f"имя для {av_id[:8]}… = {name!r}", errors)


def test_no_duplicate_ids(errors: list[str]) -> None:
    print("\n-- HEYGEN_LOOKS: нет дублей avatar_id --")
    ids = [e["id"] for e in bot.HEYGEN_LOOKS.values()]
    _assert(len(ids) == len(set(ids)), f"все id уникальны (got {len(ids)})", errors)


def test_maksim_does_not_see_smile_avatars(errors: list[str]) -> None:
    print("\n-- Максим (BRANDS[maksim].heygen_looks) НЕ видит новые аватары --")
    maksim_ids = {e.get("id") for e in (bot.BRANDS.get("maksim", {}).get("heygen_looks") or {}).values()}
    for av_id in SMILE_AVATARS:
        _assert(av_id not in maksim_ids, f"{av_id[:8]}… не в реестре Максима", errors)


def main() -> int:
    errors: list[str] = []
    for fn in (test_smile_avatars_present, test_no_duplicate_ids,
               test_maksim_does_not_see_smile_avatars):
        fn(errors)
    print("\n" + ("FAIL" if errors else "OK") + f" ({len(errors)} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
