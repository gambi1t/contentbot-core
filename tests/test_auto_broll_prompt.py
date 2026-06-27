"""TDD Ф3: промпт auto_broll де-Максимизирован и берёт палитру/контекст per-tenant.

Было: хардкод «картинг + глэмпинг Life Drive» + «accent #ff5722» в _build_prompt.
Стало: палитра из style_contract активного тенанта, контекст автора из
ai_video_broll._default_persona. panferov → Nox Dark azure + Артём; default → оранж + Максим.

Запуск: python -m pytest tests/test_auto_broll_prompt.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auto_broll  # noqa: E402

SCRIPT = (
    "Сегодня показываю, как ИИ снимает с предпринимателя рутину: "
    "разберём автоматизацию и реальные инструменты по шагам."
)


def _prompt_for(tenant_id, monkeypatch):
    if tenant_id:
        monkeypatch.setenv("TENANT_ID_EXPECTED", tenant_id)
    else:
        monkeypatch.delenv("TENANT_ID_EXPECTED", raising=False)
    return auto_broll._build_prompt(SCRIPT)


def test_prompt_forbids_hex_and_mandates_colors(monkeypatch):
    """Option B: промпт велит использовать env-driven colors.*, ЗАПРЕЩАЕТ hex-литералы
    (иначе env-инъекция палитры обходится). Палитра в промпте больше НЕ зашита."""
    p = _prompt_for("panferov", monkeypatch)
    assert "colors.accent" in p, "нет правила использовать env-driven colors.*"
    assert "не вписывай hex" in p.lower(), "нет запрета хардкодить hex-литералы"
    assert "#2e9be0" not in p.lower() and "#ff5722" not in p.lower(), (
        "в промпте остался зашитый hex — палитра должна приходить из env"
    )


def test_panferov_prompt_de_maksim_persona(monkeypatch):
    p = _prompt_for("panferov", monkeypatch)
    assert "картинг" not in p.lower(), "бизнес Максима (картинг) в промпте panferov"
    assert "life drive" not in p.lower(), "бренд Максима (Life Drive) в промпте panferov"
    assert ("Артём" in p) or ("Панфёров" in p), "нет персоны Артёма"


def test_default_prompt_keeps_maksim_persona(monkeypatch):
    p = _prompt_for(None, monkeypatch)
    assert "Максим" in p, "нет персоны Максима в дефолте"


def test_palette_env_panferov_is_azure(monkeypatch):
    monkeypatch.setenv("TENANT_ID_EXPECTED", "panferov")
    env = auto_broll._palette_env()
    assert env["REMOTION_ACCENT"] == "#2E9BE0", "panferov accent не azure в env"
    assert env["REMOTION_BG"] == "#0F172A", "panferov bg не navy в env"
    assert env.get("REMOTION_ACCENT_DIM"), "нет производного accentDim"


def test_palette_env_default_is_maksim_orange(monkeypatch):
    monkeypatch.delenv("TENANT_ID_EXPECTED", raising=False)
    env = auto_broll._palette_env()
    assert env.get("REMOTION_ACCENT", "").upper() == "#FF5722", "дефолт не оранж Максима"


def test_darken_produces_darker_hex():
    d = auto_broll._darken("#2E9BE0")
    assert d.startswith("#") and len(d) == 7, f"плохой hex: {d}"
    assert d.upper() != "#2E9BE0", "не затемнил"


def test_no_brand_hex_literal_in_code():
    """auto_broll.py больше НЕ несёт литерал бренд-цвета — приходит из контракта."""
    code = (ROOT / "auto_broll.py").read_text(encoding="utf-8").lower()
    assert "#ff5722" not in code, "литерал #ff5722 остался в коде auto_broll"
    assert "#0a0a0a" not in code, "литерал #0a0a0a остался в коде auto_broll"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
