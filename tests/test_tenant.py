"""Тесты tenant-загрузчика (Phase 2a-1 миграции на contentbot-core).

Минимальный shell: load_tenant + feature_enabled + config_doctor.
Полный перенос BRANDS/providers/notion — Phase 2b. Здесь только фундамент:
декларативный конфиг тенанта + фичефлаги + валидация (config doctor).

Запуск: python tests/test_tenant.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tenant  # noqa: E402

_errs: list[str] = []


def _assert(cond, msg):
    if cond:
        print(f"  OK {msg}")
    else:
        _errs.append(msg)
        print(f"  X FAIL {msg}")


def _write(d: dict) -> Path:
    f = Path(tempfile.mktemp(suffix=".json"))
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return f


# ── load_tenant ──────────────────────────────────────────────────────────────

def test_load_valid():
    print("\n-- load valid --")
    p = _write({"tenant_id": "maksim", "features": {"carousel": True}})
    t = tenant.load_tenant(p)
    _assert(t["tenant_id"] == "maksim", "tenant_id прочитан")
    _assert(t["features"]["carousel"] is True, "features прочитаны")
    p.unlink()


def test_load_missing_file_fallback():
    print("\n-- missing file → fallback --")
    t = tenant.load_tenant(Path("/nonexistent/tenant.json"))
    # Fallback: ядро работает, опции выключены, tenant_id=default
    _assert(t.get("tenant_id") == "default", "fallback tenant_id=default")
    _assert(t.get("features") == {}, "fallback features пусты (опции выкл)")


# ── feature_enabled ──────────────────────────────────────────────────────────

def test_feature_enabled():
    print("\n-- feature_enabled --")
    t = {"tenant_id": "x", "features": {"carousel": True, "launch_monitor": False}}
    _assert(tenant.feature_enabled(t, "carousel") is True, "включённая → True")
    _assert(tenant.feature_enabled(t, "launch_monitor") is False, "выключенная → False")
    _assert(tenant.feature_enabled(t, "tg_post") is False, "не указанная → False (safe default)")
    _assert(tenant.feature_enabled({}, "carousel") is False, "пустой тенант → False")


# ── config_doctor ────────────────────────────────────────────────────────────

def test_doctor_valid():
    print("\n-- doctor: valid --")
    t = {"tenant_id": "maksim", "features": {"tg_post": True, "carousel": False}}
    problems = tenant.config_doctor(t)
    _assert(problems == [], f"валидный конфиг → нет проблем (got {problems})")


def test_doctor_missing_required():
    print("\n-- doctor: missing required --")
    problems = tenant.config_doctor({"features": {}})  # нет tenant_id
    _assert(any("tenant_id" in p for p in problems), "ловит отсутствие tenant_id")


def test_doctor_unknown_feature():
    print("\n-- doctor: unknown feature --")
    t = {"tenant_id": "x", "features": {"carousel": True, "telepathy": True}}
    problems = tenant.config_doctor(t)
    _assert(any("telepathy" in p for p in problems), "ловит неизвестный фичефлаг")
    # известные не считаются проблемой
    _assert(not any("carousel" in p for p in problems), "известный флаг не проблема")


def test_doctor_features_must_be_bool():
    print("\n-- doctor: feature value type --")
    t = {"tenant_id": "x", "features": {"carousel": "yes"}}  # строка вместо bool
    problems = tenant.config_doctor(t)
    _assert(any("carousel" in p and "bool" in p.lower() for p in problems),
            "ловит не-bool значение фичи")


def test_feature_blocked_fail_open():
    print("\n-- feature_blocked: fail-open без конфига --")
    # Переходный период: нет конфига / пустой features → НЕ блокируем,
    # иначе наивный gating отрубит работающие фичи в проде Максима.
    _assert(tenant.feature_blocked({}, "carousel") is False, "пустой тенант → НЕ блок")
    _assert(tenant.feature_blocked({"tenant_id": "x"}, "carousel") is False, "нет features → НЕ блок")
    _assert(tenant.feature_blocked({"tenant_id": "x", "features": {}}, "carousel") is False,
            "пустой features → НЕ блок")


def test_feature_blocked_explicit():
    print("\n-- feature_blocked: блок только при явном false --")
    t = {"tenant_id": "x", "features": {"carousel": False, "tg_post": True}}
    _assert(tenant.feature_blocked(t, "carousel") is True, "явно false → блок")
    _assert(tenant.feature_blocked(t, "tg_post") is False, "явно true → НЕ блок")
    _assert(tenant.feature_blocked(t, "idea_bank") is False,
            "не упомянута (но конфиг есть) → НЕ блок (мягко, переходный период)")


def test_callback_feature_map_consistency():
    print("\n-- callback→feature map: все фичи известны --")
    import bot
    for prefix, feat in bot._CALLBACK_FEATURE_MAP.items():
        _assert(feat in tenant._KNOWN_FEATURES, f"{prefix!r}→{feat!r}: фича известна")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"\n{'='*60}\nRunning {len(tests)} tenant tests\n{'='*60}")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _errs.append(f"{fn.__name__}: {e}")
            print(f"  X EXC {fn.__name__}: {e}")
    print(f"\n{'='*60}")
    print("ALL PASS" if not _errs else f"FAIL ({len(_errs)}): " + "; ".join(_errs))
    sys.exit(0 if not _errs else 1)
