"""TDD: Фаза 3 (Авто+графика микс) — чередование клипов (16 июня).

_interleave(graphics, footage) → [g0,f0,g1,f1,...] + хвост в конец. Монтаж
берёт сбалансированный префикс. Чистый хелпер тут; ветка AUTO_HF — Telethon.

Запуск: python tests/test_broll_phase3.py
"""
from __future__ import annotations
import os, sys
from pathlib import Path
os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
sys.path.insert(0, str(Path(__file__).parent.parent))
from broll.handlers import _interleave  # noqa: E402


def _a(cond, msg, errs):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond: errs.append(msg)


def main():
    e = []
    print("\n[равные длины → строгое чередование]")
    r = _interleave(["g0", "g1", "g2"], ["f0", "f1", "f2"])
    _a(r == ["g0", "f0", "g1", "f1", "g2", "f2"], f"g0,f0,g1,f1,... (got {r})", e)

    print("\n[графики больше → лишние графики в конец]")
    r = _interleave(["g0", "g1", "g2", "g3"], ["f0", "f1"])
    _a(r == ["g0", "f0", "g1", "f1", "g2", "g3"], f"хвост графики (got {r})", e)

    print("\n[видео больше → лишнее видео в конец]")
    r = _interleave(["g0"], ["f0", "f1", "f2"])
    _a(r == ["g0", "f0", "f1", "f2"], f"хвост видео (got {r})", e)

    print("\n[один пустой → второй целиком, порядок цел]")
    _a(_interleave([], ["f0", "f1"]) == ["f0", "f1"], "графика пуста → только видео", e)
    _a(_interleave(["g0", "g1"], []) == ["g0", "g1"], "видео пусто → только графика", e)
    _a(_interleave([], []) == [], "оба пусты → []", e)

    print("\n[старт с графики — сильный хук]")
    r = _interleave(["G"], ["F"])
    _a(r[0] == "G", "первым идёт график (хук)", e)

    print()
    if e:
        print(f"❌ FAIL — {len(e)}:"); [print("   -", x) for x in e]; return 1
    print("✅ ALL PASS"); return 0


if __name__ == "__main__":
    sys.exit(main())
