"""D Step 1 (GPT-5 review): layout-критик как gate.
Этот шаг готовит ИНФРАСТРУКТУРУ: resolve пути инспектора от __file__ (не HF_PROJECT —
там он может молча отсутствовать после чистки/нового деплоя); env-флаг режима
(off/advisory/strict, default=advisory); preflight (node / inspector / browser).

Сам blocking gate включается ПОЗЖЕ (Step 4) — этот файл только тестирует контракт
инфраструктуры. Run: python tests/test_hf_layout_gate_preflight.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as hf  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


# ── path resolution ──────────────────────────────────────────────────────────


def test_inspector_path_from_repo_not_hf_project(errors):
    print("\n-- inspector резолвится от __file__/tools, не от HF_PROJECT --")
    p = hf._layout_inspector_path()
    _assert(p.name == "hf_inspect_layout.mjs", f"имя файла, got {p.name}", errors)
    _assert(p.parent.name == "tools", f"в подпапке tools/, got {p.parent.name}", errors)
    # должен указывать на репо, не на HF_PROJECT
    _assert(str(p).startswith(str(Path(hf.__file__).resolve().parent)),
            f"путь от модуля hyperframes_broll, got {p}", errors)


def test_inspector_path_exists_in_repo(errors):
    print("\n-- inspector реально лежит в репо tools/ (sanity) --")
    p = hf._layout_inspector_path()
    _assert(p.exists(), f"файл найден: {p}", errors)


# ── env-флаг режима ──────────────────────────────────────────────────────────


def test_gate_mode_default_advisory(errors):
    print("\n-- HF_LAYOUT_GATE не задан → дефолт 'advisory' --")
    os.environ.pop("HF_LAYOUT_GATE", None)
    _assert(hf._layout_gate_mode() == "advisory", f"default, got {hf._layout_gate_mode()!r}", errors)


def test_gate_mode_explicit(errors):
    print("\n-- HF_LAYOUT_GATE=off/advisory/strict парсятся --")
    for mode in ("off", "advisory", "strict"):
        os.environ["HF_LAYOUT_GATE"] = mode
        _assert(hf._layout_gate_mode() == mode, f"{mode}", errors)
    os.environ["HF_LAYOUT_GATE"] = "OFF"  # case-insensitive
    _assert(hf._layout_gate_mode() == "off", "case-insensitive", errors)


def test_gate_mode_invalid_falls_back(errors):
    print("\n-- HF_LAYOUT_GATE=мусор → advisory (safe default) --")
    os.environ["HF_LAYOUT_GATE"] = "blocking"  # неподдерживаемое
    _assert(hf._layout_gate_mode() == "advisory", "мусор → advisory", errors)
    os.environ.pop("HF_LAYOUT_GATE", None)


# ── preflight ────────────────────────────────────────────────────────────────


def test_preflight_returns_tuple(errors):
    print("\n-- _layout_gate_preflight() → (ok, detail-dict) --")
    ok, detail = hf._layout_gate_preflight()
    _assert(isinstance(ok, bool), "ok=bool", errors)
    _assert(isinstance(detail, dict), "detail=dict", errors)
    for k in ("inspector_exists", "node_available", "browser_path_set"):
        _assert(k in detail, f"detail содержит {k}", errors)


def test_preflight_inspector_missing(errors):
    print("\n-- preflight ловит отсутствующий inspector --")
    # подменим путь на несуществующий
    orig = hf._layout_inspector_path
    hf._layout_inspector_path = lambda: Path(tempfile.gettempdir()) / "no_such_inspector.mjs"
    try:
        ok, detail = hf._layout_gate_preflight()
        _assert(not detail["inspector_exists"], "inspector_exists=False при отсутствии", errors)
        _assert(not ok, "ok=False если inspector нет", errors)
    finally:
        hf._layout_inspector_path = orig


def test_preflight_browser_path_check(errors):
    print("\n-- preflight проверяет HYPERFRAMES_BROWSER_PATH --")
    orig = os.environ.get("HYPERFRAMES_BROWSER_PATH")
    os.environ.pop("HYPERFRAMES_BROWSER_PATH", None)
    try:
        _, detail = hf._layout_gate_preflight()
        _assert(not detail["browser_path_set"], "browser_path_set=False без env", errors)
    finally:
        if orig is not None:
            os.environ["HYPERFRAMES_BROWSER_PATH"] = orig


# ── интеграция: _inspect_layout уважает резолв ────────────────────────────────


def test_inspect_layout_uses_resolved_path(errors):
    print("\n-- _inspect_layout использует _layout_inspector_path() (не HF_PROJECT) --")
    # если в HF_PROJECT нет файла, _inspect_layout всё равно должна найти его в репо
    # и не вернуть [] из-за отсутствия пути (баг до фикса).
    # Это санити: путь резолвится правильно. Сам subprocess мы не запускаем
    # (он требует chrome-headless-shell — это уже не unit-тест).
    p = hf._layout_inspector_path()
    _assert(p.exists(), "inspector существует на резолвенном пути", errors)


def main() -> int:
    print("=" * 60 + "\nHF layout-gate Step 1: path + mode + preflight\n" + "=" * 60)
    errors: list = []
    for fn in (test_inspector_path_from_repo_not_hf_project, test_inspector_path_exists_in_repo,
               test_gate_mode_default_advisory, test_gate_mode_explicit, test_gate_mode_invalid_falls_back,
               test_preflight_returns_tuple, test_preflight_inspector_missing,
               test_preflight_browser_path_check, test_inspect_layout_uses_resolved_path):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all layout-gate Step 1 tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
