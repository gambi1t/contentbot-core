"""Декларативный конфиг тенанта (Phase 2a-1 миграции на contentbot-core).

Один источник правды «кто этот клиент и что у него включено». Заменяет
разбросанные BRANDS-dict / .env / зашитые литералы (см. docs/03_TENANT_MODEL.md).

Phase 2a-1 — минимальный фундамент: tenant_id + features{} + валидация.
Перенос providers / notion / brands / paths сюда — Phase 2b (инкрементально).

Файл конфига: tenant.json рядом с ботом (override env TENANT_CONFIG).
Секреты НИКОГДА не здесь — они в .env/secrets.env (значения вида "env:KEY").
"""
from __future__ import annotations

import json
import os
from pathlib import Path

TENANT_CONFIG_PATH = Path(
    os.getenv("TENANT_CONFIG", str(Path(__file__).resolve().parent / "tenant.json"))
)


class TenantConfigError(Exception):
    """Конфиг тенанта отсутствует/битый/невалиден в strict-режиме.

    Бросается на старте, чтобы НЕ запускать боевого тенанта (Phase 3) на
    fallback-значениях из BRANDS/.env/старого кода — тихий fallback при
    cutover хуже явного падения (CTO-ревью C1)."""


# Обязательные ключи конфига тенанта.
_REQUIRED_KEYS = ("tenant_id", "features")

# Ключи бренда, которые ИМЕЕТ СМЫСЛ переопределять per-tenant через
# brand_overrides (provider/notion/prompt/channel/identity). Doctor ругается
# на ключи вне списка — защита от опечаток и случайного переопределения
# структурных полей (heygen_looks, platforms, статусы) (CTO-ревью N4).
_ALLOWED_BRAND_OVERRIDE_KEYS = frozenset({
    "heygen_avatar_id", "heygen_avatar_v4_id", "eleven_voice_id",
    "script_prompt_file", "cover_prompt_file", "script_prompt_override",
    "notion_db_id", "notion_rubric_property",
    "telegram_channel_handle", "telegram_channel_display",
    "description",
})

# Известные опциональные пайплайны (фичефлаги). Ядро (селфи-съёмка → сценарий →
# обложка → озвучка → аватар → сборка → субтитры → кросспост → Notion →
# библиотека) включено всегда и флагов НЕ имеет. Здесь — только опции
# конструктора (из инвентаризации обоих ботов 10 июня).
_KNOWN_FEATURES = frozenset({
    "tg_post",          # /tgpost — посты для канала
    "carousel",         # IG-карусели
    "idea_bank",        # банк идей → меню пайплайнов
    "launch_monitor",   # виральный поиск (зарубежные источники)
    "youtube_broll",    # поиск/нарезка B-roll с YouTube
    "hyperframes",       # HyperFrames-графика
    "remotion",         # Remotion-графика
    "image_gen",        # /image (Nano Banana Pro)
    "video_gen",        # /video (Kling)
    "instagram_dm",     # comment-to-DM воронка
    "billing",          # биллинг-гейт + баланс
})


def load_tenant(path: str | Path | None = None, strict: bool = False) -> dict:
    """Прочитать конфиг тенанта.

    Нет файла:
      - transitional (strict=False) → безопасный fallback {tenant_id:default,
        features:{}}: ядро работает, опции выключены (прод без tenant.json цел);
      - strict (Phase 3) → TenantConfigError (боевой тенант ОБЯЗАН иметь конфиг).
    Битый JSON → TenantConfigError с путём (friendly, CTO-ревью I2) — вместо
    «голого» JSONDecodeError, чтобы при cutover сразу понять причину и откатиться.
    """
    p = Path(path) if path is not None else TENANT_CONFIG_PATH
    if not p.is_file():
        if strict:
            raise TenantConfigError(f"tenant config not found (strict mode): {p}")
        return {"tenant_id": "default", "features": {}}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise TenantConfigError(f"invalid tenant config {p}: {e}") from e


def feature_enabled(tenant: dict, name: str) -> bool:
    """Включена ли опциональная фича у тенанта. Не указана → False (safe
    default: новый клиент не получает фичу, пока её явно не включили)."""
    return bool((tenant.get("features") or {}).get(name, False))


def feature_blocked(tenant: dict, name: str) -> bool:
    """Надо ли ЗАБЛОКИРОВАТЬ опц-фичу для этого тенанта.

    Блокируем ТОЛЬКО если конфиг ЯВНО выключил фичу (`name: false`).
    Нет конфига / пустой features / фича не упомянута → НЕ блокируем
    (fail-open). Это переходный период: пока tenant.json не задеплоен на
    сервер, gating не должен ломать уже работающие боты. Ужесточение
    (блок неупомянутых известных фич) — отдельным решением в Phase 2b.

    Отличается от `feature_enabled` намеренно: enabled — «показать ли
    кнопку/возможность» (default OFF), blocked — «отклонить ли уже пришедший
    вызов» (default ALLOW, чтобы не сломать прод без конфига).
    """
    feats = tenant.get("features")
    if not isinstance(feats, dict) or not feats:
        return False
    return feats.get(name) is False


def _resolve_env(value):
    """'env:KEY' → os.environ.get('KEY') (None если переменной нет).
    Любое другое значение возвращается как есть."""
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:])
    return value


def apply_brand_overrides(brand: dict, tenant: dict, brand_name: str) -> dict:
    """Тонкий слой Phase 2a-3 (вариант Б): tenant.json МОЖЕТ переопределить
    поля активного бренда (provider IDs, notion, промпт-файлы и т.п.), а
    BRANDS-dict остаётся fallback.

    Нет `brand_overrides` / нет записи для этого бренда → возвращает brand
    БЕЗ изменений (прод без tenant.json не меняется). Значения `env:KEY`
    резолвятся из окружения; если переменной нет (None) — поле НЕ затирается
    (остаётся значение из бренда). Вход НЕ мутируется.
    """
    overrides = (tenant.get("brand_overrides") or {}).get(brand_name)
    if not overrides:
        return brand
    merged = dict(brand)
    for k, v in overrides.items():
        rv = _resolve_env(v)
        if rv is not None:
            merged[k] = rv
    return merged


def allowed_brands(tenant: dict) -> list[str] | None:
    """Бренды, видимые тенанту в /brand-пикере. None = без ограничений
    (transitional / ключ не задан → показываем все, прод не меняется).

    Нужно после слияния в core: BRANDS-dict содержит бренды ВСЕХ тенантов
    (default+shoes+maksim), и без фильтра panferov увидел бы чужой бренд
    maksim в пикере (CTO-ревью I3). Конфиг: {"brands": {"allowed": [...]}}.
    """
    brands = tenant.get("brands")
    if not isinstance(brands, dict):
        return None
    allowed = brands.get("allowed")
    if isinstance(allowed, list) and allowed:
        return [str(b) for b in allowed]
    return None


def brand_allowed(tenant: dict, name: str) -> bool:
    """Разрешён ли бренд тенанту. Нет ограничений (None) → True (все бренды —
    прод без конфига не меняется)."""
    allowed = allowed_brands(tenant)
    return True if allowed is None else name in allowed


def config_doctor(
    tenant: dict,
    *,
    expected_id: str | None = None,
    known_brands: list[str] | None = None,
    base_dir: str | Path | None = None,
    strict: bool = False,
) -> list[str]:
    """Проверка конфига ДО запуска. Возвращает список проблем (пусто = ok).

    Базовый набор (Phase 2a-1, всегда): required keys, известность фичефлагов,
    тип значения фичи, override-ключи в allowlist.

    Опциональные проверки (Phase 2c, по переданным параметрам — обратная
    совместимость с `config_doctor(tenant)`):
      - expected_id: tenant_id должен совпасть (CTO-ревью C5);
      - known_brands: brand_overrides ссылается на существующий бренд (I4);
      - base_dir: prompt-файлы из override существуют (I4);
      - strict: все `env:KEY` из brand_overrides резолвятся (C2).
    """
    problems: list[str] = []
    for k in _REQUIRED_KEYS:
        if k not in tenant:
            problems.append(f"missing required key: {k}")
    feats = tenant.get("features")
    if feats is not None and not isinstance(feats, dict):
        problems.append("features must be an object")
        feats = {}
    for fname, fval in (feats or {}).items():
        if fname not in _KNOWN_FEATURES:
            problems.append(f"unknown feature flag: {fname}")
        if not isinstance(fval, bool):
            problems.append(f"feature {fname!r} must be bool, got {type(fval).__name__}")

    if expected_id is not None and tenant.get("tenant_id") != expected_id:
        problems.append(
            f"tenant_id mismatch: expected {expected_id!r}, got {tenant.get('tenant_id')!r}"
        )

    base = Path(base_dir) if base_dir is not None else None
    overrides = tenant.get("brand_overrides") or {}
    if not isinstance(overrides, dict):
        problems.append("brand_overrides must be an object")
        overrides = {}
    for brand_name, fields in overrides.items():
        if known_brands is not None and brand_name not in known_brands:
            problems.append(
                f"brand_overrides references unknown brand: {brand_name!r} "
                f"(known: {known_brands})"
            )
        if not isinstance(fields, dict):
            problems.append(f"brand_overrides.{brand_name} must be an object")
            continue
        for key, val in fields.items():
            if key not in _ALLOWED_BRAND_OVERRIDE_KEYS:
                problems.append(f"brand_overrides.{brand_name}.{key}: key not in allowlist")
            # env:KEY резолв — в strict обязателен
            if strict and isinstance(val, str) and val.startswith("env:"):
                env_key = val[4:]
                if not os.getenv(env_key):
                    problems.append(
                        f"missing env var for brand_overrides.{brand_name}.{key}: {env_key}"
                    )
            # prompt-файлы существуют (если задан base_dir)
            if base is not None and key in ("script_prompt_file", "cover_prompt_file"):
                resolved = _resolve_env(val)
                if isinstance(resolved, str) and resolved and not (base / resolved).is_file():
                    problems.append(
                        f"brand_overrides.{brand_name}.{key}: file not found: {resolved}"
                    )
    return problems
