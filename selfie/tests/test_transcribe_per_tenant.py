"""TDD: per-tenant Whisper-промпт (порт M2).

panferov (Артём) → AI-инструменты, БЕЗ Максим/картинг/глэмпинг.
maksim/default → текущий «Максим Юмсунов…» (backward-compat).

Run: python selfie/tests/test_transcribe_per_tenant.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from selfie.transcribe import build_whisper_prompt  # noqa: E402

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


def test_default_keeps_maksim_prompt():
    print("\n-- default → промпт Максима (backward-compat) --")
    _set(None)
    p = build_whisper_prompt()
    _assert("Юмсунов" in p, "default: содержит Юмсунова")
    _assert("ChatGPT" in p, "default: содержит AI-инструменты")
    _assert(len(p) <= 300, f"default: длина <=300 ({len(p)})")


def test_panferov_prompt_ai_no_maksim():
    print("\n-- panferov → AI Артёма, без Максим/картинг --")
    _set("panferov")
    p = build_whisper_prompt()
    _assert("ChatGPT" in p and "Claude" in p and "Cursor" in p, "panferov: AI-инструменты")
    _assert("Панфёров" in p, "panferov: контекст Артёма")
    _assert("Юмсунов" not in p and "картинг" not in p and "глэмпинг" not in p,
            "panferov: НЕТ Максим/картинг/глэмпинг")
    _assert(50 <= len(p) <= 300, f"panferov: длина ок ({len(p)})")


if __name__ == "__main__":
    _set(None)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"{'='*60}\nRunning {len(tests)} transcribe per-tenant tests\n{'='*60}")
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
