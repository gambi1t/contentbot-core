"""C1 (HyperFrames per-tenant тема): тематический бриф в промптах стирает
«трассу»/картинг у panferov и переосмысляет path_map как дата-пайплайн.
Раньше единственная тема была Life Drive (картинг+глэмпинг) → graphics уходили
в дорожно-картинговые образы даже у panferov (Артём 22.06).

Run: python tests/test_hf_tenant_theme.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import tenant  # noqa: E402
import hyperframes_broll as hf  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _with_tenant(tid: str):
    tenant.active_tenant_id = lambda: tid


def test_panferov_brief_ai_no_trassa(errors):
    print("\n-- panferov бриф: AI-тема, запрет трассы/картинга, path_map=пайплайн --")
    _with_tenant("panferov")
    b = hf._tenant_theme_brief().lower()
    _assert("пайплайн" in b and ("ai" in b or "ии" in b), "AI/пайплайн мир", errors)
    _assert("запрещ" in b and "трасс" in b, "запрещает трассу", errors)
    _assert("картинг" in b and "глэмпинг" in b, "картинг/глэмпинг в запретных", errors)
    _assert("life drive" in b, "Life Drive помечен чужим брендом", errors)
    _assert("дата-пайплайн" in b and "не извилистая дорога" in b, "path_map переосмыслен", errors)


def test_maksim_brief_is_karting(errors):
    print("\n-- maksim/default бриф: картинг-тема, БЕЗ запрета картинга --")
    _with_tenant("maksim")
    b = hf._tenant_theme_brief().lower()
    _assert("картинг" in b and "глэмпинг" in b, "картинг/глэмпинг = тема", errors)
    _assert("запрещ" not in b or "трасс" not in b, "не запрещает свою же тему", errors)


def test_brief_in_storyboard_prompt(errors):
    print("\n-- бриф попадает в промпт раскадровки (panferov) --")
    _with_tenant("panferov")
    p = hf._build_storyboard_prompt("Сценарий про AI-инструменты и автоматизацию контента.")
    _assert("ЗАПРЕЩЁННЫЕ ОБРАЗЫ" in p, "запретные образы в промпте", errors)
    _assert("трасс" in p.lower() and "пайплайн" in p.lower(), "трасса/пайплайн в промпте", errors)
    _assert("ТЕМА БРЕНДА" in p, "блок темы в промпте", errors)


def test_storyboard_prompt_switches_by_tenant(errors):
    print("\n-- промпт раскадровки меняется по тенанту --")
    _with_tenant("maksim")
    pm = hf._build_storyboard_prompt("Сценарий про сезонный бизнес.")
    _assert("картинг" in pm.lower() and "ЗАПРЕЩЁННЫЕ ОБРАЗЫ" not in pm,
            "maksim-промпт = картинг, без запрета", errors)


def main() -> int:
    print("=" * 60 + "\nHyperFrames per-tenant theme brief (C1)\n" + "=" * 60)
    errors: list = []
    _orig = tenant.active_tenant_id
    try:
        for fn in (test_panferov_brief_ai_no_trassa, test_maksim_brief_is_karting,
                   test_brief_in_storyboard_prompt, test_storyboard_prompt_switches_by_tenant):
            fn(errors)
    finally:
        tenant.active_tenant_id = _orig
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all hf-tenant-theme tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
