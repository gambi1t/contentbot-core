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

# Обязательные ключи конфига тенанта.
_REQUIRED_KEYS = ("tenant_id", "features")

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


def load_tenant(path: str | Path | None = None) -> dict:
    """Прочитать конфиг тенанта. Если файла нет — безопасный fallback:
    ядро работает, опции выключены, tenant_id='default'."""
    p = Path(path) if path is not None else TENANT_CONFIG_PATH
    if not p.is_file():
        return {"tenant_id": "default", "features": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


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


def config_doctor(tenant: dict) -> list[str]:
    """Проверка конфига ДО запуска. Возвращает список проблем (пусто = ok).

    Минимальный набор (Phase 2a-1): required keys, известность фичефлагов,
    тип значения фичи. Проверки provider/notion/файлов — Phase 2b.
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
    return problems
