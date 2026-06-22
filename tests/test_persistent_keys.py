"""CTO-ревью P0 #4: guard, что перезаписи pending между этапами selfie НЕ теряют
критичные ключи. Потеря notion_page_id → дубль Notion-карточки; selfie_words →
ре-транскрибация; selfie_finished → сломанный durable-маркер/trim-guard.

Единый источник — PERSISTENT_SELFIE_KEYS + carry_session_keys (handlers.py).
Если кто-то добавит новую перезапись pending и забудет пронос — этот тест + сам
helper держат инвариант.

telegram/тяжёлое мокаем. Run: python tests/test_persistent_keys.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

for _m in ("telegram", "telegram.ext", "selfie.broll_picker", "selfie.cover",
           "selfie.music", "selfie.edit", "selfie.transcribe"):
    sys.modules.setdefault(_m, MagicMock())
sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie import handlers as sh  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_critical_keys_present(errors):
    print("\n-- PERSISTENT_SELFIE_KEYS содержит критичные --")
    for k in ("notion_page_id", "selfie_words", "selfie_finished", "card_data"):
        _assert(k in sh.PERSISTENT_SELFIE_KEYS, f"{k} в наборе", errors)


def test_carry_preserves_all(errors):
    print("\n-- carry_session_keys: проносит ВСЕ persistent-ключи через перезапись --")
    prev = {k: f"val_{k}" for k in sh.PERSISTENT_SELFIE_KEYS}
    prev["state"] = "selfie_text_review"  # не-persistent
    new = {"state": "selfie_broll_offer"}  # «новый этап» с нуля
    sh.carry_session_keys(prev, new)
    for k in sh.PERSISTENT_SELFIE_KEYS:
        _assert(new.get(k) == f"val_{k}", f"{k} пронесён", errors)
    _assert(new["state"] == "selfie_broll_offer", "не-persistent (state) НЕ перезатёрт", errors)


def test_carry_skips_none_and_missing(errors):
    print("\n-- carry_session_keys: None/отсутствующие не засоряют new --")
    prev = {"notion_page_id": "pid", "selfie_words": None}  # words=None, остальных нет
    new = {"state": "x"}
    sh.carry_session_keys(prev, new)
    _assert(new.get("notion_page_id") == "pid", "реальное значение пронесено", errors)
    _assert("selfie_words" not in new, "None НЕ пронесён", errors)
    _assert("card_data" not in new, "отсутствующий НЕ добавлен", errors)


def test_carry_empty_prev_safe(errors):
    print("\n-- carry_session_keys: пустой/None prev не падает --")
    try:
        sh.carry_session_keys({}, {"state": "x"})
        sh.carry_session_keys(None, {"state": "x"})
        _assert(True, "no-op без prev", errors)
    except Exception as e:
        _assert(False, f"бросил {e}", errors)


def main() -> int:
    print("=" * 60 + "\nselfie pending persistent-keys guard (P0 #4)\n" + "=" * 60)
    errors: list = []
    for fn in (test_critical_keys_present, test_carry_preserves_all,
               test_carry_skips_none_and_missing, test_carry_empty_prev_safe):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all persistent-keys tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
