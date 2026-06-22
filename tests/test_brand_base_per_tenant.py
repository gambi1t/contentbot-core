"""Fix B: библиотека B-roll per-tenant — у каждого тенанта своя папка
``<root>/<tenant>/<cat>``. panferov НЕ должен видеть картинг maksim (раньше путь
был захардкожен ``root/maksim`` для всех). Для dev/default — историческая папка
maksim (фикстуры репо), backward-compat с test_broll_categories/test_library_manager.

telegram мокаем. Run: python tests/test_brand_base_per_tenant.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.modules.setdefault("telegram", MagicMock())
sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie import broll_picker as bp  # noqa: E402
import tenant  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def run(errors: list) -> None:
    orig_roots = bp._LIBRARY_ROOTS
    orig_tid = tenant.active_tenant_id
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "maksim").mkdir()
        bp._LIBRARY_ROOTS = {"image": root, "video": root}
        try:
            print("\n-- maksim тенант → своя папка maksim --")
            tenant.active_tenant_id = lambda: "maksim"
            _assert(bp._brand_base("image") == root / "maksim", "maksim → root/maksim", errors)

            print("\n-- panferov без своей папки → СТРОГО panferov (без утечки maksim) --")
            tenant.active_tenant_id = lambda: "panferov"
            bb = bp._brand_base("image")
            _assert(bb == root / "panferov", "panferov → root/panferov", errors)
            _assert(bb != root / "maksim", "panferov НЕ видит maksim (no leak)", errors)
            _assert(not bb.exists(), "несуществующая → scan=[] (Fix A graceful)", errors)

            print("\n-- panferov со своей папкой → она --")
            (root / "panferov").mkdir()
            _assert(bp._brand_base("image") == root / "panferov", "panferov → root/panferov", errors)

            print("\n-- default/dev → legacy maksim (backward-compat тестов) --")
            tenant.active_tenant_id = lambda: "default"
            _assert(bp._brand_base("image") == root / "maksim", "default → legacy maksim", errors)

            print("\n-- ошибка tenant → legacy maksim (не падаем) --")
            def _boom():
                raise RuntimeError("no tenant")
            tenant.active_tenant_id = _boom
            _assert(bp._brand_base("image") == root / "maksim", "exception → legacy maksim", errors)

            print("\n-- default без maksim-папки → плоский корень --")
            tenant.active_tenant_id = lambda: "default"
            with tempfile.TemporaryDirectory() as td2:
                root2 = Path(td2)
                bp._LIBRARY_ROOTS = {"image": root2, "video": root2}
                _assert(bp._brand_base("image") == root2, "нет maksim → root (flat)", errors)
        finally:
            bp._LIBRARY_ROOTS = orig_roots
            tenant.active_tenant_id = orig_tid


def main() -> int:
    print("=" * 60 + "\nbroll library _brand_base — per-tenant (Fix B)\n" + "=" * 60)
    errors: list = []
    run(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all brand-base tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
