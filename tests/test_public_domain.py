"""Per-tenant публичный домен (tenant.public_domain) — для OAuth-redirect и
media/covers-URL. Регрессия 23.06: redirect/media захардкожены на maksim-bot →
panferov OAuth-колбэк уходил на сервер Максима, Instagram тянул video_url с домена
Максима → YouTube/Instagram падали. Оригинальный content-bot: bot.panferov-ai.ru.

Run: python tests/test_public_domain.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import tenant  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _with_tenant(tid):
    tenant.active_tenant_id = lambda: tid


def test_env_override(errors):
    print("\n-- BOT_PUBLIC_DOMAIN задан → возвращается он (override) --")
    os.environ["BOT_PUBLIC_DOMAIN"] = "bot.panferov-ai.ru"
    _assert(tenant.public_domain() == "bot.panferov-ai.ru", "домен из env override", errors)
    os.environ.pop("BOT_PUBLIC_DOMAIN", None)


def test_panferov_by_tenant_id_no_env(errors):
    print("\n-- БЕЗ env: panferov → bot.panferov-ai.ru (нет тихого fallback на Максима) --")
    os.environ.pop("BOT_PUBLIC_DOMAIN", None)
    _with_tenant("panferov")
    _assert(tenant.public_domain() == "bot.panferov-ai.ru",
            "panferov домен по tenant_id без env", errors)


def test_maksim_default_no_env(errors):
    print("\n-- БЕЗ env: maksim/default → maksim-bot (Максим не ломается) --")
    os.environ.pop("BOT_PUBLIC_DOMAIN", None)
    _with_tenant("maksim")
    _assert(tenant.public_domain() == "maksim-bot.panferov-ai.ru", "maksim → maksim-bot", errors)
    _with_tenant("default")
    _assert(tenant.public_domain() == "maksim-bot.panferov-ai.ru", "default → maksim-bot", errors)


def test_invalid_env_raises(errors):
    print("\n-- мусорный BOT_PUBLIC_DOMAIN (со схемой/слешем) → ошибка --")
    for bad in ("https://bot.panferov-ai.ru", "bot.panferov-ai.ru/oauth", "host:443"):
        os.environ["BOT_PUBLIC_DOMAIN"] = bad
        try:
            tenant.public_domain()
            _assert(False, f"должно падать на {bad!r}", errors)
        except ValueError:
            _assert(True, f"ловит мусор {bad!r}", errors)
    os.environ.pop("BOT_PUBLIC_DOMAIN", None)


def test_redirect_uri_shape(errors):
    print("\n-- из домена строится корректный OAuth-redirect (panferov) --")
    os.environ["BOT_PUBLIC_DOMAIN"] = "bot.panferov-ai.ru"
    d = tenant.public_domain()
    _assert(f"https://{d}/oauth/callback" == "https://bot.panferov-ai.ru/oauth/callback",
            "YouTube redirect = твой домен", errors)
    _assert(f"https://{d}/oauth/vk/callback" == "https://bot.panferov-ai.ru/oauth/vk/callback",
            "VK redirect = твой домен", errors)
    os.environ.pop("BOT_PUBLIC_DOMAIN", None)


def main():
    print("=" * 60 + "\nper-tenant public_domain (crosspost OAuth/media fix)\n" + "=" * 60)
    errors = []
    _orig = os.environ.get("BOT_PUBLIC_DOMAIN")
    _orig_tid = tenant.active_tenant_id
    try:
        for fn in (test_env_override, test_panferov_by_tenant_id_no_env,
                   test_maksim_default_no_env, test_invalid_env_raises, test_redirect_uri_shape):
            fn(errors)
    finally:
        tenant.active_tenant_id = _orig_tid
        if _orig is None:
            os.environ.pop("BOT_PUBLIC_DOMAIN", None)
        else:
            os.environ["BOT_PUBLIC_DOMAIN"] = _orig
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all public-domain tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
