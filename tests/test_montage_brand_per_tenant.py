"""TDD: brand_name для assemble_auto_montage выводится из активного тенанта.

Баг: selfie/handlers.py слал brand_name="maksim" ЖЁСТКО → panferov-ролики
получали Максимову геометрию аватара (crop_y fallback 260 вместо дефолтных 280,
video_assembler._avatar_crop_y). Фикс: brand_name = tenant.active_tenant_id()
(panferov→"panferov", maksim→"maksim", default→"default"), как бренд-лексикон в
subtitle_burner._active_canonical. Не ломаем Максима (его 260 сохраняется).

Гейт по env TENANT_ID_EXPECTED (tenant.active_tenant_id()).

Run: python tests/test_montage_brand_per_tenant.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from video_assembler import (  # noqa: E402
    DEFAULT_AVATAR_CROP_Y,
    _avatar_crop_y,
    montage_brand_name,
)

_errs: list[str] = []


def _assert(cond, msg):
    print(f"  {'OK' if cond else 'X FAIL'} {msg}")
    if not cond:
        _errs.append(msg)


def _set_tenant(tid):
    if tid is None:
        os.environ.pop("TENANT_ID_EXPECTED", None)
    else:
        os.environ["TENANT_ID_EXPECTED"] = tid


def test_resolves_tenant_id():
    print("\n-- montage_brand_name() = active_tenant_id (panferov→свой) --")
    _set_tenant("maksim")
    _assert(montage_brand_name() == "maksim", "maksim → 'maksim'")
    _set_tenant("panferov")
    _assert(montage_brand_name() == "panferov", "panferov → 'panferov' (НЕ 'maksim')")
    _set_tenant("default")
    _assert(montage_brand_name() == "default", "default → 'default'")
    _set_tenant(None)
    _assert(montage_brand_name() == "default", "нет env → 'default' (fallback)")


def test_crop_y_consequence():
    print("\n-- crop_y fallback по выведенному бренду (суть бага) --")
    # Максим сохраняет своё (голова выше в split): 260, не дефолт.
    _assert(_avatar_crop_y("maksim") == 260, "maksim crop_y fallback = 260")
    _assert(DEFAULT_AVATAR_CROP_Y == 280, "DEFAULT_AVATAR_CROP_Y = 280")

    _set_tenant("panferov")
    cy_panferov = _avatar_crop_y(montage_brand_name())
    _assert(cy_panferov == DEFAULT_AVATAR_CROP_Y,
            f"panferov crop_y fallback = {DEFAULT_AVATAR_CROP_Y} (был баг: 260)")
    _assert(cy_panferov != 260, "panferov НЕ получает Максимовы 260")

    _set_tenant("maksim")
    _assert(_avatar_crop_y(montage_brand_name()) == 260,
            "maksim crop_y fallback по резолву = 260 (не сломан)")

    _set_tenant("default")
    _assert(_avatar_crop_y(montage_brand_name()) == DEFAULT_AVATAR_CROP_Y,
            "default crop_y fallback = 280")
    _set_tenant(None)


def test_never_resolves_to_shoes():
    print("\n-- ни один тенант не резолвится в 'shoes' (Ken Burns не триггерится) --")
    for tid in ("maksim", "panferov", "default", None):
        _set_tenant(tid)
        _assert(montage_brand_name() != "shoes",
                f"tenant={tid!r} → brand != 'shoes'")
    _set_tenant(None)


if __name__ == "__main__":
    _set_tenant(None)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"{'='*60}\nRunning {len(tests)} montage-brand-per-tenant tests\n{'='*60}")
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
