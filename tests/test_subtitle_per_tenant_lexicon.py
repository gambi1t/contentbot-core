"""TDD: per-tenant brand-лексикон в субтитрах (порт M2).

Артём (panferov) = AI-лексикон (Cursor/Opus/Grok/…), БЕЗ бизнес-терминов
Максима. Максим/default = AI + бизнес (моржа→маржа/глэмпинг/lifedrive) —
текущее поведение (backward-compat). Гейт по tenant.active_tenant_id()
(env TENANT_ID_EXPECTED).

Run: python tests/test_subtitle_per_tenant_lexicon.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from subtitle_burner import fix_brand_names  # noqa: E402

_errs: list[str] = []


def _assert(cond, msg):
    print(f"  {'OK' if cond else 'X FAIL'} {msg}")
    if not cond:
        _errs.append(msg)


def _w(t):
    return [{"word": t, "start": 0.0, "end": 0.4}]


def _word(words):
    return [x["word"] for x in words]


def _set_tenant(tid):
    if tid is None:
        os.environ.pop("TENANT_ID_EXPECTED", None)
    else:
        os.environ["TENANT_ID_EXPECTED"] = tid


def test_default_keeps_business():
    print("\n-- default (нет тенанта): AI + бизнес (backward-compat) --")
    _set_tenant(None)
    _assert(_word(fix_brand_names(_w("моржа"))) == ["маржа"], "default: моржа→маржа")
    _assert(_word(fix_brand_names(_w("меджорни"))) == ["Midjourney"], "default: меджорни→Midjourney")


def test_maksim_keeps_business():
    print("\n-- maksim: AI + бизнес --")
    _set_tenant("maksim")
    _assert(_word(fix_brand_names(_w("моржа"))) == ["маржа"], "maksim: моржа→маржа")
    _assert(_word(fix_brand_names(_w("глемпинг"))) == ["глэмпинг"], "maksim: глемпинг→глэмпинг")


def test_panferov_no_maksim_business():
    print("\n-- panferov: бизнес-термины Максима НЕ применяются --")
    _set_tenant("panferov")
    _assert(_word(fix_brand_names(_w("моржа"))) == ["моржа"], "panferov: моржа НЕ→маржа")
    _assert(_word(fix_brand_names(_w("глемпинг"))) == ["глемпинг"], "panferov: глемпинг НЕ→глэмпинг")
    _assert(_word(fix_brand_names(_w("лайфдрайв"))) == ["лайфдрайв"], "panferov: лайфдрайв НЕ→Life Drive")


def test_panferov_full_ai_lexicon():
    print("\n-- panferov: полный AI-лексикон Артёма восстановлен --")
    _set_tenant("panferov")
    _assert(_word(fix_brand_names(_w("курсор"))) == ["Cursor"], "panferov: курсор→Cursor")
    _assert(_word(fix_brand_names(_w("кьюрсор"))) == ["Cursor"], "panferov: кьюрсор→Cursor")
    _assert(_word(fix_brand_names(_w("грок"))) == ["Grok"], "panferov: грок→Grok")
    _assert(_word(fix_brand_names(_w("опус"))) == ["Opus"], "panferov: опус→Opus")
    _assert(_word(fix_brand_names(_w("меджорни"))) == ["Midjourney"], "panferov: меджорни→Midjourney (AI база)")


def test_active_tenant_id_reads_env():
    print("\n-- tenant.active_tenant_id() из env --")
    import tenant
    _set_tenant("panferov")
    _assert(tenant.active_tenant_id() == "panferov", "active_tenant_id=panferov")
    _set_tenant("maksim")
    _assert(tenant.active_tenant_id() == "maksim", "active_tenant_id=maksim")


if __name__ == "__main__":
    _set_tenant(None)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"{'='*60}\nRunning {len(tests)} per-tenant lexicon tests\n{'='*60}")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _errs.append(f"{fn.__name__}: {e}")
            print(f"  X EXC {fn.__name__}: {e}")
    _set_tenant(None)
    print(f"\n{'='*60}")
    print("ALL PASS" if not _errs else f"FAIL ({len(_errs)}): " + "; ".join(_errs))
    sys.exit(0 if not _errs else 1)
