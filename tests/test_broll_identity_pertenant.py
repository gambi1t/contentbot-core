"""Phase A / High 2 + Medium 2: identity B-roll per-tenant.

- bridge_broll_to_publication писал хардкод "brand":"maksim" в pending panferov →
  заменено на канонический card_brand + tenant_id раздельно.
- UI-строки «голос Максима» → бренд-aware (_voiced_by_phrase / _ai_voice_choice_label):
  maksim видит имя, panferov — нейтрально.

Запуск: python tests/test_broll_identity_pertenant.py
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

import tenant as _tenant  # noqa: E402
from broll import handlers as bh  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(("  OK " if cond else "  FAIL ") + safe)
    if not cond:
        errors.append(safe)


def _with_tenant(tid, fn):
    orig = _tenant.active_tenant_id
    try:
        _tenant.active_tenant_id = lambda: tid
        return fn()
    finally:
        _tenant.active_tenant_id = orig


def test_voice_phrase_per_tenant(errors: list[str]) -> None:
    print("\n-- _voiced_by_phrase / _ai_voice_choice_label per-tenant --")
    _assert(_with_tenant("maksim", bh._voiced_by_phrase) == "голосом Максима",
            "maksim → «голосом Максима»", errors)
    _assert(_with_tenant("panferov", bh._voiced_by_phrase) == "ИИ-голосом",
            "panferov → «ИИ-голосом» (нет имени Максима)", errors)
    _assert("Максима" in _with_tenant("maksim", bh._ai_voice_choice_label),
            "maksim-кнопка с именем", errors)
    _assert("Максим" not in _with_tenant("panferov", bh._ai_voice_choice_label),
            "panferov-кнопка БЕЗ имени Максима", errors)


def test_bridge_card_brand_not_hardcoded(errors: list[str]) -> None:
    print("\n-- bridge: card_brand + tenant_id, нет хардкода \"brand\":\"maksim\" --")
    src = Path(bh.__file__).read_text(encoding="utf-8")
    _assert('"card_brand"' in src, "пишется card_brand", errors)
    _assert('"tenant_id"' in src, "пишется tenant_id (раздельно)", errors)
    _assert('"brand": "maksim"' not in src, "нет хардкода \"brand\":\"maksim\"", errors)


def test_no_raw_maksim_user_strings(errors: list[str]) -> None:
    print("\n-- нет сырых user-facing «Максима» (только через бренд-aware хелперы) --")
    src = Path(bh.__file__).read_text(encoding="utf-8")
    # сырая кнопка/строка с именем НЕ должна торчать вне хелперов
    _assert('InlineKeyboardButton("🤖 Голос Максима' not in src,
            "кнопка озвучки не хардкодит имя", errors)
    _assert("озвучка голосом Максима" not in src,
            "preview не хардкодит «голосом Максима»", errors)


def main() -> int:
    errors: list[str] = []
    for fn in (test_voice_phrase_per_tenant, test_bridge_card_brand_not_hardcoded,
               test_no_raw_maksim_user_strings):
        fn(errors)
    print("\n" + ("FAIL" if errors else "OK") + f" ({len(errors)} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
