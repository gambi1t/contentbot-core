"""Webshare residential proxy pool — for yt-dlp YouTube bypass.

Hetzner datacenter IPs trigger YouTube's "Sign in to confirm you're not a bot"
on popular videos.  Cookies don't fix it — only a residential IP does.  We use
Webshare's Static Residential plan ($6/mo, 20 IPs through real ISPs like
Comcast, AT&T, Telecom Italia, Vorboss UK, etc.).

Public API:

  ``get_random_proxy()`` → ``"http://user:pass@host:port"`` or ``None``

The list is fetched once via Webshare's API and cached in-memory for an hour
so that monthly auto-refresh and manual replacements get picked up without a
restart.  Set ``WEBSHARE_API_KEY`` in ``.env`` to enable; absent or empty key
disables the pool (yt-dlp falls back to direct connection — which is the old
behavior).
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Optional

import requests

logger = logging.getLogger("webshare_proxy")

WEBSHARE_API = "https://proxy.webshare.io/api/v2/proxy/list/"
CACHE_TTL_SEC = 3600  # 1 hour — Webshare refreshes IPs no more often than monthly

# In-memory cache: list of {"username", "password", "proxy_address", "port", ...}
_proxy_cache: list[dict] = []
_cache_fetched_at: float = 0.0


def _api_key() -> str:
    return (os.getenv("WEBSHARE_API_KEY") or "").strip()


def _is_enabled() -> bool:
    return bool(_api_key())


def _fetch_proxy_list(force: bool = False) -> list[dict]:
    """Pull the live proxy list from Webshare. Returns cached copy on failure."""
    global _proxy_cache, _cache_fetched_at

    if not _is_enabled():
        return []

    age = time.time() - _cache_fetched_at
    if not force and _proxy_cache and age < CACHE_TTL_SEC:
        return _proxy_cache

    try:
        res = requests.get(
            WEBSHARE_API,
            headers={"Authorization": f"Token {_api_key()}"},
            params={"mode": "direct", "page": 1, "page_size": 100},
            timeout=15,
        )
        res.raise_for_status()
        data = res.json()
        results = data.get("results", []) or []
        # Keep only valid proxies (Webshare marks down ones it couldn't verify)
        valid = [p for p in results if p.get("valid", True)]
        if not valid:
            logger.warning(
                f"[webshare] API returned {len(results)} proxies but none valid; "
                "keeping previous cache"
            )
            return _proxy_cache
        _proxy_cache = valid
        _cache_fetched_at = time.time()
        countries = sorted({p.get("country_code", "??") for p in valid})
        logger.info(
            f"[webshare] fetched {len(valid)} valid proxies "
            f"(countries: {','.join(countries)})"
        )
        return _proxy_cache
    except Exception as e:
        logger.warning(f"[webshare] fetch failed: {e}; falling back to cache (size={len(_proxy_cache)})")
        return _proxy_cache


def get_random_proxy() -> Optional[str]:
    """Return a yt-dlp-compatible proxy URL, or None if pool unavailable.

    Format: ``http://user:pass@host:port`` — works as ``--proxy`` arg for
    yt-dlp, and as ``proxies={"http": ..., "https": ...}`` for ``requests``
    (wrap the same string for both keys).
    """
    if not _is_enabled():
        return None
    pool = _fetch_proxy_list()
    if not pool:
        return None
    p = random.choice(pool)
    return f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"


def get_proxy_count() -> int:
    """Diagnostic: how many proxies are currently in the cache."""
    return len(_proxy_cache)


def force_refresh() -> int:
    """Force-fetch the list from Webshare API. Returns new pool size."""
    _fetch_proxy_list(force=True)
    return len(_proxy_cache)
