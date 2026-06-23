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


def test_panferov_brief_ai_context_diversity(errors):
    print("\n-- panferov бриф: AI-контекст канала + разнообразие, БЕЗ чёрного списка --")
    _with_tenant("panferov")
    b = hf._tenant_theme_brief()
    bl = b.lower()
    _assert("ai" in bl or "ии" in bl, "AI-контекст канала", errors)
    _assert("разнообр" in bl, "явная установка на разнообразие", errors)
    _assert("от смысла" in bl or "от смысл" in bl, "веди визуал от смысла сценария", errors)
    _assert("life drive" in bl, "Life Drive обозначен как заточка под другого клиента (анти-инерция)", errors)
    # Анти-регресс: НЕ должно быть чёрного списка / категорических запретов образов
    _assert("запрещённые образы" not in bl and "🔴" not in b,
            "БЕЗ чёрного списка образов (Артём 23.06: не запрещаем дорогу/природу/деньги)", errors)


def test_maksim_brief_is_karting_context(errors):
    print("\n-- maksim/default бриф: картинг-контекст канала --")
    _with_tenant("maksim")
    bl = hf._tenant_theme_brief().lower()
    _assert("картинг" in bl and "глэмпинг" in bl, "картинг/глэмпинг = контекст канала", errors)
    _assert("запрещённые" not in bl, "без чёрного списка", errors)


def test_brief_in_storyboard_prompt(errors):
    print("\n-- бриф попадает в промпт раскадровки (panferov) --")
    _with_tenant("panferov")
    p = hf._build_storyboard_prompt("Сценарий про AI-инструменты и автоматизацию контента.")
    _assert("КОНТЕКСТ КАНАЛА" in p, "блок контекста в промпте", errors)
    _assert("разнообр" in p.lower(), "разнообразие в промпте", errors)


def test_storyboard_prompt_switches_by_tenant(errors):
    print("\n-- промпт раскадровки меняется по тенанту --")
    _with_tenant("maksim")
    pm = hf._build_storyboard_prompt("Сценарий про сезонный бизнес.")
    _assert("картинг" in pm.lower(), "maksim-промпт = картинг-контекст", errors)
    _with_tenant("panferov")
    pp = hf._build_storyboard_prompt("Сценарий про AI.")
    _assert("картинг" not in pp.lower() or "не подставляй" in pp.lower(),
            "panferov-промпт не лепит картинг по инерции", errors)


def main() -> int:
    print("=" * 60 + "\nHyperFrames per-tenant theme brief (C1)\n" + "=" * 60)
    errors: list = []
    _orig = tenant.active_tenant_id
    try:
        for fn in (test_panferov_brief_ai_context_diversity, test_maksim_brief_is_karting_context,
                   test_brief_in_storyboard_prompt, test_storyboard_prompt_switches_by_tenant):
            fn(errors)
    finally:
        tenant.active_tenant_id = _orig
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all hf-tenant-theme tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
