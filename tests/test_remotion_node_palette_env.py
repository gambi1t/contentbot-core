"""TDD (Option B): Node-проект Remotion берёт бренд-палитру из env per-tenant.

Бренд-палитра НЕ зашита в проекте — впрыскивается через REMOTION_*-env при
рендере (ОДИН общий проект, как HyperFrames из style_contract). fonts.ts и
design-tokens.ts читают process.env.REMOTION_*; шаблоны берут бренд-цвет из
env-driven POSTULAT.accent, без литеральных бренд-rgba. Функциональные цвета
терминала (#27c93f/#ff5f56) — целы. Это снимает деплой-блокер (общий проект
безопасен для Максима: без env → дефолт-оранж).

Запуск: python -m pytest tests/test_remotion_node_palette_env.py -v
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = Path(os.getenv("BROLL_PROJECT_DIR") or (ROOT.parent / "panferov-broll"))

TOKEN_FILES = [NODE / "src" / "fonts.ts", NODE / "src" / "design-tokens.ts"]
TEMPLATE_FILES = [
    NODE / "src" / "templates" / "AiToolDeepDive.tsx",
    NODE / "src" / "templates" / "AiProductLaunch.tsx",
]

pytestmark = pytest.mark.skipif(
    not NODE.exists(), reason=f"Node-проект Remotion не найден: {NODE}"
)


def test_tokens_are_env_driven():
    for f in TOKEN_FILES:
        txt = f.read_text(encoding="utf-8")
        assert "process.env.REMOTION_ACCENT" in txt, f"{f.name}: accent не из REMOTION_ACCENT"
        assert "process.env.REMOTION_BG" in txt, f"{f.name}: bg не из REMOTION_BG"


def test_templates_no_hardcoded_brand_rgba():
    for f in TEMPLATE_FILES:
        txt = f.read_text(encoding="utf-8")
        assert "rgba(255,87,34" not in txt, f"{f.name}: остался оранж-rgba (бренд зашит)"
        assert "rgba(46,155,224" not in txt, f"{f.name}: остался azure-rgba (должно быть POSTULAT.accent)"
        assert "POSTULAT.accent" in txt, f"{f.name}: не использует env-driven POSTULAT.accent"


def test_functional_terminal_colors_preserved():
    joined = "\n".join(
        f.read_text(encoding="utf-8") for f in TEMPLATE_FILES if f.exists()
    ).lower()
    assert "#27c93f" in joined, "потерян функциональный зелёный терминала (#27c93f)"
    assert "#ff5f56" in joined, "потерян функциональный красный терминала (#ff5f56)"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
