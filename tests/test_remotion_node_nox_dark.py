"""TDD-страж Ф0: Node-проект Remotion перекрашен в Nox Dark (panferov), не оранж Постулата.

Источники цвета проекта (panferov-broll): src/fonts.ts (colors),
src/design-tokens.ts (POSTULAT/ACCENTS), шаблоны экранов AiToolDeepDive/AiProductLaunch.
Бренд-акцент panferov = #2E9BE0 (azure, из style_contract.panferov.json), НЕ #ff5722
(оранж Максима/Постулата) и НЕ его dim #cc3e15.

Функциональные цвета терминала (#27c93f зелёный, #ff5f56 красный, #ffbd2e жёлтый,
#ffd700 gold) — это «настоящий экран», НЕ бренд: перекрашивать нельзя (иначе фейк-экран
перестаёт читаться как настоящий). Тест проверяет, что они на месте.

Запуск: python -m pytest tests/test_remotion_node_nox_dark.py -v
"""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = Path(os.getenv("BROLL_PROJECT_DIR") or (ROOT.parent / "panferov-broll"))

COLOR_FILES = [NODE / "src" / "fonts.ts", NODE / "src" / "design-tokens.ts"]
TEMPLATE_FILES = [
    NODE / "src" / "templates" / "AiToolDeepDive.tsx",
    NODE / "src" / "templates" / "AiProductLaunch.tsx",
]

# Бренд-оранж Постулата: акцент и его dim. #ff5f56 (красный светофор) НЕ попадает.
_ORANGE_HEX = re.compile(r"#ff5722\b|#cc3e15\b", re.IGNORECASE)
_ORANGE_RGBA = re.compile(r"rgba\(\s*255\s*,\s*87\s*,\s*34")
_AZURE = "#2e9be0"

pytestmark = pytest.mark.skipif(
    not NODE.exists(), reason=f"Node-проект Remotion не найден: {NODE}"
)


def test_color_tokens_no_orange():
    for f in COLOR_FILES:
        txt = f.read_text(encoding="utf-8")
        assert not _ORANGE_HEX.search(txt), (
            f"{f.name}: остался оранж Постулата (#ff5722/#cc3e15) — "
            f"в значениях или комментариях"
        )


def test_color_tokens_have_azure():
    for f in COLOR_FILES:
        txt = f.read_text(encoding="utf-8").lower()
        assert _AZURE in txt, f"{f.name}: нет azure-акцента {_AZURE}"


def test_templates_no_orange_literals():
    for f in TEMPLATE_FILES:
        txt = f.read_text(encoding="utf-8")
        assert not _ORANGE_HEX.search(txt), f"{f.name}: литеральный оранж #ff5722/#cc3e15"
        assert not _ORANGE_RGBA.search(txt), f"{f.name}: rgba(255,87,34,...) — бренд-оранж"


def test_functional_terminal_colors_preserved():
    """Функциональные цвета терминала НЕ должны быть стёрты перекраской."""
    joined = "\n".join(
        f.read_text(encoding="utf-8") for f in TEMPLATE_FILES if f.exists()
    ).lower()
    assert "#27c93f" in joined, "потерян функциональный зелёный терминала (#27c93f)"
    assert "#ff5f56" in joined, "потерян функциональный красный терминала (#ff5f56)"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
