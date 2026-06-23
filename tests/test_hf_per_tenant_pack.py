"""C2 (per-tenant reference_pack резолв): `reference_pack.<tenant>.md` если есть,
иначе дефолт `reference_pack.md`. Симметрично style_contract (load_style_contract).

Запускается без HF_PROJECT prod-зависимостей — патчит HF_PROJECT на временный путь.

Run: python tests/test_hf_per_tenant_pack.py
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

import tenant  # noqa: E402
import hyperframes_broll as hf  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _with_tenant(tid: str):
    tenant.active_tenant_id = lambda: tid


def run(errors: list) -> None:
    _orig_hf = hf.HF_PROJECT
    _orig_tid = tenant.active_tenant_id
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        hf.HF_PROJECT = root
        try:
            print("\n-- нет per-tenant и нет default → fallback на default-путь (даже если файла нет)")
            _with_tenant("panferov")
            _assert(hf._active_reference_pack_path() == root / "reference_pack.md",
                    "fallback path = default (panferov.md нет)", errors)

            print("\n-- только default есть → отдаёт default (для maksim/panferov)")
            (root / "reference_pack.md").write_text("DEFAULT PACK", encoding="utf-8")
            _with_tenant("maksim")
            _assert(hf._active_reference_pack_path() == root / "reference_pack.md", "maksim → default", errors)
            _with_tenant("panferov")
            _assert(hf._active_reference_pack_path() == root / "reference_pack.md", "panferov → default (своего нет)", errors)

            print("\n-- panferov.md существует → panferov резолвится в свой пак")
            (root / "reference_pack.panferov.md").write_text("PANFEROV PACK", encoding="utf-8")
            _with_tenant("panferov")
            _assert(hf._active_reference_pack_path() == root / "reference_pack.panferov.md",
                    "panferov → reference_pack.panferov.md", errors)
            _with_tenant("maksim")
            _assert(hf._active_reference_pack_path() == root / "reference_pack.md",
                    "maksim не утаскивает panferov.md", errors)

            print("\n-- tenant.active_tenant_id бросает → default (no leak / no crash)")
            def _boom():
                raise RuntimeError("no tenant")
            tenant.active_tenant_id = _boom
            _assert(hf._active_reference_pack_path() == root / "reference_pack.md",
                    "исключение → default", errors)

            print("\n-- _load_inline_refs читает per-tenant контент")
            (root / "index.html").write_text("<html>SAMPLE</html>", encoding="utf-8")
            _with_tenant("panferov")
            ref, idx = hf._load_inline_refs()
            _assert(ref == "PANFEROV PACK", f"panferov: контент panferov-пака, got {ref!r}", errors)
            _assert("SAMPLE" in idx, "index sample подхвачен", errors)
            _with_tenant("maksim")
            ref_m, _ = hf._load_inline_refs()
            _assert(ref_m == "DEFAULT PACK", f"maksim: контент default-пака, got {ref_m!r}", errors)
        finally:
            hf.HF_PROJECT = _orig_hf
            tenant.active_tenant_id = _orig_tid


def main() -> int:
    print("=" * 60 + "\nHF per-tenant reference_pack resolve (C2)\n" + "=" * 60)
    errors: list = []
    run(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all hf-per-tenant-pack tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
