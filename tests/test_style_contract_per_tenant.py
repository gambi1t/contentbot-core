"""TDD: per-tenant style contract (HF срез C, шаг 2).

panferov → hyperframes_assets/style_contract.panferov.json (Nox Dark);
maksim/default → style_contract.json (дефолт). Generic: style_contract.<tid>.json
если есть, иначе дефолт. Через tenant.active_tenant_id() (как M2).

Run: python tests/test_style_contract_per_tenant.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import style_contract  # noqa: E402

ASSETS = Path(style_contract.__file__).resolve().parent / "hyperframes_assets"
_errs: list[str] = []


def _assert(cond, msg):
    print(f"  {'OK' if cond else 'X FAIL'} {msg}")
    if not cond:
        _errs.append(msg)


def _set(t):
    if t is None:
        os.environ.pop("TENANT_ID_EXPECTED", None)
    else:
        os.environ["TENANT_ID_EXPECTED"] = t


def _accent(d):
    return d["palette"]["accent"]


def _file_accent(name):
    return _accent(json.loads((ASSETS / name).read_text(encoding="utf-8")))


def test_contracts_actually_differ():
    print("\n-- palette panferov != default (иначе тест бессмыслен) --")
    _assert(_file_accent("style_contract.json") != _file_accent("style_contract.panferov.json"),
            f"default {_file_accent('style_contract.json')} != panferov {_file_accent('style_contract.panferov.json')}")


def test_panferov_uses_panferov_contract():
    print("\n-- panferov → style_contract.panferov.json --")
    _set("panferov")
    got = _accent(style_contract.load_style_contract())
    _assert(got == _file_accent("style_contract.panferov.json"), f"panferov accent={got}")


def test_default_and_maksim_use_default():
    print("\n-- default/maksim → style_contract.json (backward-compat) --")
    exp = _file_accent("style_contract.json")
    _set(None)
    _assert(_accent(style_contract.load_style_contract()) == exp, "default → дефолтный контракт")
    _set("maksim")
    _assert(_accent(style_contract.load_style_contract()) == exp, "maksim → дефолтный контракт (нет style_contract.maksim.json)")


def test_explicit_path_honored():
    print("\n-- явный path перебивает тенант --")
    _set("panferov")
    got = _accent(style_contract.load_style_contract(ASSETS / "style_contract.json"))
    _assert(got == _file_accent("style_contract.json"), "explicit path → именно тот файл")


if __name__ == "__main__":
    _set(None)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"{'='*60}\nRunning {len(tests)} style-contract per-tenant tests\n{'='*60}")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _errs.append(f"{fn.__name__}: {e}")
            print(f"  X EXC {fn.__name__}: {e}")
    _set(None)
    print(f"\n{'='*60}")
    print("ALL PASS" if not _errs else f"FAIL ({len(_errs)}): " + "; ".join(_errs))
    sys.exit(0 if not _errs else 1)
