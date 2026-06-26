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


def test_panferov_prompt_is_azure_and_de_maksim(monkeypatch):
    p = _prompt_for("panferov", monkeypatch)
    assert "#2E9BE0" in p, "нет azure-акцента panferov в промпте"
    assert "#ff5722" not in p.lower(), "оранж Максима протёк в промпт panferov"
    assert "картинг" not in p.lower(), "бизнес Максима (картинг) в промпте panferov"
    assert "life drive" not in p.lower(), "бренд Максима (Life Drive) в промпте panferov"
    assert ("Артём" in p) or ("Панфёров" in p), "нет персоны Артёма"


def test_default_prompt_keeps_maksim_orange(monkeypatch):
    p = _prompt_for(None, monkeypatch)
    assert "#FF5722" in p.upper(), "дефолт-контракт (Максим) потерял оранж"
    assert "Максим" in p, "нет персоны Максима в дефолте"


def test_no_brand_hex_literal_in_code():
    """auto_broll.py больше НЕ несёт литерал бренд-цвета — приходит из контракта."""
    code = (ROOT / "auto_broll.py").read_text(encoding="utf-8").lower()
    assert "#ff5722" not in code, "литерал #ff5722 остался в коде auto_broll"
    assert "#0a0a0a" not in code, "литерал #0a0a0a остался в коде auto_broll"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
