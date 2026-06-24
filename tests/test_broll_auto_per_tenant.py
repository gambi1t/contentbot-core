"""Phase A / High 4: AUTO-источник per-tenant + graceful пустой архив.

Было: AUTO/AUTO_HF звали select_clips БЕЗ clips_root → хардкод
DEFAULT_CLIPS_ROOT=.../clips/maksim → panferov читал бы клипы МАКСИМА (cross-tenant
leak). Пустой архив → тупиковая ошибка вместо возврата к меню источника.

Стало: clips_root резолвится per-tenant через _brand_base("video"); на пустом
архиве (SelectorError) — graceful возврат source_menu_keyboard.

Запуск: python tests/test_broll_auto_per_tenant.py
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

import tenant as _tenant  # noqa: E402
from selfie import broll_picker as bp  # noqa: E402
from broll import selector as sel  # noqa: E402
from broll import handlers as bh  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(("  OK " if cond else "  FAIL ") + safe)
    if not cond:
        errors.append(safe)


def test_brand_base_per_tenant(errors: list[str]) -> None:
    print("\n-- _brand_base('video') резолвит per-tenant (нет cross-tenant leak) --")
    orig = _tenant.active_tenant_id
    try:
        _tenant.active_tenant_id = lambda: "panferov"
        p = bp._brand_base("video")
        _assert(p is not None and p.name == "panferov", f"panferov → .../panferov (got {p})", errors)
        _tenant.active_tenant_id = lambda: "maksim"
        m = bp._brand_base("video")
        _assert(m is not None and m.name == "maksim", f"maksim → .../maksim (got {m})", errors)
        _assert(str(p) != str(m), "panferov и maksim — РАЗНЫЕ папки", errors)
    finally:
        _tenant.active_tenant_id = orig


def test_empty_root_raises_no_maksim_fallback(errors: list[str]) -> None:
    print("\n-- пустой clips_root → SelectorError (НЕ тихий фоллбэк на maksim) --")
    with tempfile.TemporaryDirectory() as td:
        try:
            sel.select_clips("тест сценарий", None, clips_root=Path(td))
            _assert(False, "должен бросить SelectorError на пустом архиве", errors)
        except sel.SelectorError:
            _assert(True, "SelectorError на пустом архиве (без LLM)", errors)


def test_auto_branch_resolves_root_and_graceful(errors: list[str]) -> None:
    print("\n-- handlers AUTO: clips_root per-tenant + graceful меню на SelectorError --")
    src = Path(bh.__file__).read_text(encoding="utf-8")
    idx = src.find("if mode == SourceMode.AUTO:")
    _assert(idx != -1, "ветка AUTO найдена", errors)
    if idx == -1:
        return
    window = src[idx: idx + 1200]
    _assert("_brand_base" in window and "clips_root" in window,
            "AUTO резолвит clips_root через _brand_base", errors)
    _assert("source_menu_keyboard" in window,
            "AUTO на пустом архиве возвращает source_menu_keyboard (graceful)", errors)


def main() -> int:
    errors: list[str] = []
    for fn in (test_brand_base_per_tenant, test_empty_root_raises_no_maksim_fallback,
               test_auto_branch_resolves_root_and_graceful):
        fn(errors)
    print("\n" + ("FAIL" if errors else "OK") + f" ({len(errors)} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
