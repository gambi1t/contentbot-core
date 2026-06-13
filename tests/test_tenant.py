"""Тесты tenant-загрузчика (Phase 2a-1 миграции на contentbot-core).

Минимальный shell: load_tenant + feature_enabled + config_doctor.
Полный перенос BRANDS/providers/notion — Phase 2b. Здесь только фундамент:
декларативный конфиг тенанта + фичефлаги + валидация (config doctor).

Запуск: python tests/test_tenant.py
"""
from __future__ import annotations

import json
import os
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


def test_apply_brand_overrides_fallback():
    print("\n-- apply_brand_overrides: fallback без конфига --")
    brand = {"heygen_avatar_id": "AAA", "eleven_voice_id": "VVV"}
    # Нет brand_overrides / нет конфига → бренд НЕ меняется (прод цел).
    _assert(tenant.apply_brand_overrides(brand, {}, "maksim") == brand, "пустой тенант → бренд как есть")
    _assert(tenant.apply_brand_overrides(brand, {"brand_overrides": {}}, "maksim") == brand,
            "пустой brand_overrides → как есть")
    _assert(tenant.apply_brand_overrides(brand, {"brand_overrides": {"other": {"x": 1}}}, "maksim") == brand,
            "override для ДРУГОГО бренда → текущий не тронут")
    # Не мутирует вход
    tenant.apply_brand_overrides(brand, {"brand_overrides": {"maksim": {"heygen_avatar_id": "NEW"}}}, "maksim")
    _assert(brand["heygen_avatar_id"] == "AAA", "вход НЕ мутируется")


def test_apply_brand_overrides_merge():
    print("\n-- apply_brand_overrides: переопределение полей --")
    brand = {"heygen_avatar_id": "AAA", "eleven_voice_id": "VVV", "description": "old"}
    t = {"brand_overrides": {"maksim": {"heygen_avatar_id": "NEW", "description": "новый"}}}
    out = tenant.apply_brand_overrides(brand, t, "maksim")
    _assert(out["heygen_avatar_id"] == "NEW", "поле переопределено")
    _assert(out["description"] == "новый", "второе поле переопределено")
    _assert(out["eleven_voice_id"] == "VVV", "неуказанное поле сохранено из бренда")


def test_apply_brand_overrides_env():
    print("\n-- apply_brand_overrides: env:KEY резолв --")
    os.environ["_TEST_AVATAR"] = "FROM_ENV"
    brand = {"heygen_avatar_id": "AAA"}
    t = {"brand_overrides": {"maksim": {"heygen_avatar_id": "env:_TEST_AVATAR"}}}
    out = tenant.apply_brand_overrides(brand, t, "maksim")
    _assert(out["heygen_avatar_id"] == "FROM_ENV", "env:KEY резолвится из окружения")
    # env-переменной НЕТ → НЕ затираем бренд (безопасно)
    t2 = {"brand_overrides": {"maksim": {"heygen_avatar_id": "env:_MISSING_VAR_XYZ"}}}
    out2 = tenant.apply_brand_overrides(brand, t2, "maksim")
    _assert(out2["heygen_avatar_id"] == "AAA", "env: без значения → fallback на бренд (не None)")
    del os.environ["_TEST_AVATAR"]


def test_callback_feature_map_consistency():
    print("\n-- callback→feature map: все фичи известны --")
    import bot
    for prefix, feat in bot._CALLBACK_FEATURE_MAP.items():
        _assert(feat in tenant._KNOWN_FEATURES, f"{prefix!r}→{feat!r}: фича известна")


def _looks_like_secret(val) -> bool:
    """Эвристика leak-guard: длинная hex/base64-строка без префикса env: —
    подозрение на боевой provider ID / токен, которому не место в git."""
    import re
    if not isinstance(val, str) or val.startswith("env:"):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{16,}", val)) and not val.startswith("_")


def test_example_configs_no_secrets_leak():
    """Phase 2a-4 leak-guard: example-конфиги в git НЕ должны содержать
    боевых provider ID / токенов — только env:KEY-ссылки. Тот же класс
    риска, что пойманный 11 июня NOTION_TOKEN. Полноценные leakage-тесты
    ДАННЫХ — в Phase 2b, когда конфиги наполнятся."""
    print("\n-- leak-guard: нет голых секретов в example-конфигах --")
    examples = sorted((ROOT / "tenants").glob("*.example.json"))
    _assert(len(examples) >= 2, f"найдены example-конфиги ({len(examples)})")
    for f in examples:
        cfg = json.loads(f.read_text(encoding="utf-8"))
        # config_doctor чистый
        _assert(tenant.config_doctor(cfg) == [], f"{f.name}: config_doctor чисто")
        # ни одно значение в brand_overrides не выглядит как голый секрет
        for brand, fields in (cfg.get("brand_overrides") or {}).items():
            for k, v in (fields or {}).items():
                _assert(not _looks_like_secret(v),
                        f"{f.name}: {brand}.{k} не голый ID (={v!r})")


def test_example_configs_distinct():
    """Конфиги тенантов должны РАЗЛИЧАТЬСЯ (не копипаст одного в другой) —
    иначе тенантизация фиктивна. Минимальная проверка: разные tenant_id
    и хоть один различающийся фичефлаг."""
    print("\n-- конфиги тенантов различимы --")
    mk = json.loads((ROOT / "tenants" / "maksim.example.json").read_text(encoding="utf-8"))
    pf = json.loads((ROOT / "tenants" / "panferov.example.json").read_text(encoding="utf-8"))
    _assert(mk["tenant_id"] != pf["tenant_id"], "tenant_id различны")
    _assert(mk.get("features") != pf.get("features"), "наборы фич различны (не копипаст)")


# ── Phase 2c-1: strict-режим + config_doctor глубже ─────────────────────────

def test_load_strict_no_file_raises():
    print("\n-- strict + нет файла → fatal --")
    try:
        tenant.load_tenant(Path("/nonexistent/tenant.json"), strict=True)
        _assert(False, "должно бросить TenantConfigError")
    except tenant.TenantConfigError:
        _assert(True, "strict + нет файла → TenantConfigError")


def test_load_no_strict_no_file_fallback_unchanged():
    print("\n-- БЕЗ strict + нет файла → fallback (прод цел) --")
    t = tenant.load_tenant(Path("/nonexistent/tenant.json"))
    _assert(t.get("tenant_id") == "default", "transitional fallback не сломан")


def test_load_bad_json_raises_friendly():
    print("\n-- битый JSON → friendly TenantConfigError --")
    f = Path(tempfile.mktemp(suffix=".json"))
    f.write_text("{ broken json ,,", encoding="utf-8")
    try:
        tenant.load_tenant(f)
        _assert(False, "должно бросить TenantConfigError")
    except tenant.TenantConfigError as e:
        _assert("invalid tenant config" in str(e).lower() or str(f) in str(e),
                "friendly error с путём к файлу")
    finally:
        f.unlink()


def test_doctor_expected_id_mismatch():
    print("\n-- config_doctor: tenant_id != expected --")
    t = {"tenant_id": "maksim", "features": {}}
    probs = tenant.config_doctor(t, expected_id="panferov")
    _assert(any("expected" in p and "panferov" in p for p in probs), "mismatch → problem")
    probs_ok = tenant.config_doctor({"tenant_id": "panferov", "features": {}}, expected_id="panferov")
    _assert(not any("expected" in p for p in probs_ok), "совпадение → нет problem")


def test_doctor_strict_missing_env():
    print("\n-- config_doctor strict: env:KEY без переменной → problem --")
    t = {"tenant_id": "panferov", "features": {},
         "brand_overrides": {"default": {"heygen_avatar_id": "env:_DOCTOR_MISSING_XYZ"}}}
    probs = tenant.config_doctor(t, strict=True)
    _assert(any("_DOCTOR_MISSING_XYZ" in p for p in probs), "missing env → problem (strict)")
    # БЕЗ strict — не ругаемся (transitional)
    probs_soft = tenant.config_doctor(t, strict=False)
    _assert(not any("_DOCTOR_MISSING_XYZ" in p for p in probs_soft), "без strict — env не обязателен")


def test_doctor_missing_prompt_file():
    print("\n-- config_doctor: prompt-файл не существует → problem --")
    t = {"tenant_id": "panferov", "features": {},
         "brand_overrides": {"default": {"script_prompt_file": "no_such_prompt_xyz.txt"}}}
    probs = tenant.config_doctor(t, base_dir=ROOT)
    _assert(any("no_such_prompt_xyz.txt" in p for p in probs), "несуществующий prompt-файл → problem")


def test_doctor_brand_override_unknown_brand():
    print("\n-- config_doctor: override для несуществующего бренда → problem --")
    t = {"tenant_id": "panferov", "features": {},
         "brand_overrides": {"ghostbrand": {"eleven_voice_id": "x"}}}
    probs = tenant.config_doctor(t, known_brands=["default", "shoes"])
    _assert(any("ghostbrand" in p for p in probs), "неизвестный бренд → problem")


def test_doctor_override_key_not_allowed():
    print("\n-- config_doctor: override-ключ вне allowlist → problem --")
    t = {"tenant_id": "panferov", "features": {},
         "brand_overrides": {"default": {"__evil_key__": "x"}}}
    probs = tenant.config_doctor(t)
    _assert(any("__evil_key__" in p for p in probs), "ключ вне allowlist → problem")


def test_doctor_transitional_backcompat():
    print("\n-- config_doctor(tenant) без новых параметров — старое поведение --")
    # Существующий вызов bot.py: config_doctor(tenant) — не должен ломаться
    probs = tenant.config_doctor({"tenant_id": "maksim", "features": {"carousel": True}})
    _assert(probs == [], "валидный конфиг без strict → чисто (как раньше)")


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
