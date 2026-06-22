"""U2: prepare_broll_in_project пропускает клипы с пропавшим источником (напр.
почищенный gen-dir) вместо FileNotFoundError на весь монтаж. Защитный пояс к U1
(после провала сборки gen-клипы могли исчезнуть — но монтаж не должен падать).

telegram мокаем. Run: python tests/test_prepare_broll_guard.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.modules.setdefault("telegram", MagicMock())
sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie import broll_picker as bp  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def run(errors: list) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        good = root / "good.mp4"
        good.write_bytes(b"x" * 10)
        missing = root / "gone.mp4"  # НЕ создаём — имитируем почищенный gen-dir
        proj = root / "assembly"
        proj.mkdir()
        items = [
            bp.BrollItem(kind="video", source=good, label="[HF] ok"),
            bp.BrollItem(kind="video", source=missing, label="[HF] gone"),
        ]
        print("\n-- prepare_broll_in_project: пропавший источник пропущен, не падает --")
        try:
            bp.prepare_broll_in_project(items, proj)
            _assert(True, "не бросил FileNotFoundError на пропавший клип", errors)
        except Exception as e:
            _assert(False, f"бросил {type(e).__name__}: {e}", errors)
            return
        copied = sorted(p.name for p in proj.glob("broll_*.mp4"))
        _assert(copied == ["broll_001.mp4"], f"скопирован только живой клип: {copied}", errors)


def main() -> int:
    print("=" * 60 + "\nprepare_broll_in_project — existence guard (U2)\n" + "=" * 60)
    errors: list = []
    run(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK prepare-broll-guard test passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
