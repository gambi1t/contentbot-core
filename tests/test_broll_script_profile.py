"""Phase A / High 3: де-бренд промпта сценария B-roll (per-brand voice profile).

Было: broll/llm.py:_SYSTEM жёстко про «Life Drive / Максим Юмсунов / картинг» —
для panferov генерил мусор. Нарушает закон core/style (ядро без бренд-литералов).

Стало: нейтральный _SYSTEM_TEMPLATE + BRAND_VOICE_PROFILES {maksim, default};
resolve_voice_profile(brand) raise на unknown (НЕ выбираем чужую персону молча);
llm.py НЕ импортирует bot (хендлер передаёт brand_name снапшотом).

Запуск: python tests/test_broll_script_profile.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from broll import llm as broll_llm  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(("  OK " if cond else "  FAIL ") + safe)
    if not cond:
        errors.append(safe)


def test_default_profile_is_panferov_not_maksim(errors: list[str]) -> None:
    print("\n-- профиль default (panferov): Артём/ИИ, НЕ картинг/Максим --")
    sysmsg = broll_llm._build_system(broll_llm.resolve_voice_profile("default")).lower()
    _assert("артём" in sysmsg or "панфёров" in sysmsg or "ии" in sysmsg,
            "default-промпт про Артёма/ИИ", errors)
    for bad in ("картинг", "глэмпинг", "life drive", "максим юмсунов", "тюмень"):
        _assert(bad not in sysmsg, f"default-промпт без «{bad}»", errors)


def test_maksim_profile_keeps_brand(errors: list[str]) -> None:
    print("\n-- профиль maksim: бренд сохранён (не сломали Максима) --")
    sysmsg = broll_llm._build_system(broll_llm.resolve_voice_profile("maksim")).lower()
    _assert("максим" in sysmsg, "maksim-промпт про Максима", errors)
    _assert("картинг" in sysmsg or "life drive" in sysmsg, "maksim-промпт про бренд", errors)


def test_unknown_brand_raises(errors: list[str]) -> None:
    print("\n-- неизвестный бренд → ValueError (не молчаливая чужая персона) --")
    try:
        broll_llm.resolve_voice_profile("totally_unknown_brand")
        _assert(False, "должен бросить ValueError", errors)
    except ValueError:
        _assert(True, "ValueError на unknown бренде", errors)


def test_template_is_brand_neutral(errors: list[str]) -> None:
    print("\n-- сам _SYSTEM_TEMPLATE без бренд-литералов (закон core/style) --")
    tpl = broll_llm._SYSTEM_TEMPLATE.lower()
    for bad in ("картинг", "глэмпинг", "максим", "life drive", "артём"):
        _assert(bad not in tpl, f"шаблон без «{bad}» (только из профиля)", errors)


def test_llm_does_not_import_bot(errors: list[str]) -> None:
    print("\n-- broll/llm.py НЕ импортирует bot (нет обратной зависимости) --")
    src = Path(broll_llm.__file__).read_text(encoding="utf-8")
    _assert("import bot" not in src and "from bot " not in src,
            "llm.py без import bot", errors)


def test_generate_script_requires_brand(errors: list[str]) -> None:
    print("\n-- generate_script принимает brand_name (резолвит профиль) --")
    import inspect
    sig = inspect.signature(broll_llm.generate_script)
    _assert("brand_name" in sig.parameters, "generate_script(brand_name=...) есть", errors)


def main() -> int:
    errors: list[str] = []
    for fn in (test_default_profile_is_panferov_not_maksim, test_maksim_profile_keeps_brand,
               test_unknown_brand_raises, test_template_is_brand_neutral,
               test_llm_does_not_import_bot, test_generate_script_requires_brand):
        fn(errors)
    print("\n" + ("FAIL" if errors else "OK") + f" ({len(errors)} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
