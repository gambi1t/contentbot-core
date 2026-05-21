"""Smoke-тест broll.selector.select_clips.

Тест 1 — реальный Claude (Sonnet) выбирает клипы под сценарий про картинг.
Тест 2 — фоллбэк без LLM (claude=None).

Оба прогоняются на локальном server-mirror архиве. Проверки:
  - вернулось n_min..n_max путей
  - все пути существуют и это видеофайлы
  - порядок — список (упорядоченный)

Запуск:  python _smoke_test_broll_selector.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from broll.selector import select_clips, SelectorError

BASE = Path(__file__).parent
ARCHIVE = BASE / "clips-to-upload" / "_server_mirror" / "clips" / "maksim"

SCRIPT = (
    "Скорость — это про управление, а не про адреналин. На картинг-трассе "
    "характер человека виден за тридцать секунд: кто-то давит газ вслепую, "
    "кто-то читает поворот заранее. В бизнесе всё то же самое. Резкий старт "
    "без расчёта — и ты в отбойнике. Привезёт не самый быстрый, а самый "
    "ровный. Life Drive — попробуй пройти свой круг чисто."
)


def _check(clips: list[Path], n_min: int, n_max: int, label: str) -> bool:
    ok = True
    if not isinstance(clips, list):
        print(f"  FAIL [{label}]: вернулся не список")
        return False
    if not (n_min <= len(clips) <= n_max):
        print(f"  FAIL [{label}]: {len(clips)} клипов, ожидалось {n_min}-{n_max}")
        ok = False
    else:
        print(f"  OK [{label}]: {len(clips)} клипов")
    for c in clips:
        if not Path(c).exists():
            print(f"  FAIL [{label}]: путь не существует — {c}")
            ok = False
        elif Path(c).suffix.lower() not in (".mov", ".mp4"):
            print(f"  FAIL [{label}]: не видеофайл — {c}")
            ok = False
    if len(set(map(str, clips))) != len(clips):
        print(f"  FAIL [{label}]: есть дубли путей")
        ok = False
    for c in clips:
        print(f"     {Path(c).parent.name}/{Path(c).name}")
    return ok


def main() -> int:
    load_dotenv(override=True)
    if not ARCHIVE.is_dir():
        print(f"FAIL: архив не найден — {ARCHIVE}")
        return 1

    n_min, n_max = 5, 9
    overall = True

    # ── Тест 1: реальный Claude ──
    print("Тест 1 — Claude (Sonnet) ранжирует клипы:")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic
            claude = anthropic.Anthropic(api_key=api_key)
            clips = select_clips(SCRIPT, claude, n_min, n_max, clips_root=ARCHIVE)
            overall &= _check(clips, n_min, n_max, "LLM")
        except Exception as e:
            print(f"  FAIL [LLM]: {type(e).__name__}: {e}")
            overall = False
    else:
        print("  SKIP: нет ANTHROPIC_API_KEY в .env")

    # ── Тест 2: фоллбэк ──
    print("\nТест 2 — фоллбэк без LLM (claude=None):")
    try:
        clips_fb = select_clips(SCRIPT, None, n_min, n_max, clips_root=ARCHIVE)
        overall &= _check(clips_fb, n_min, n_max, "fallback")
    except Exception as e:
        print(f"  FAIL [fallback]: {type(e).__name__}: {e}")
        overall = False

    # ── Тест 3: пустой архив → SelectorError ──
    print("\nТест 3 — пустой архив бросает SelectorError:")
    try:
        select_clips(SCRIPT, None, n_min, n_max, clips_root=BASE / "_nonexistent_xyz")
        print("  FAIL: ожидался SelectorError")
        overall = False
    except SelectorError:
        print("  OK: SelectorError брошен")
    except Exception as e:
        print(f"  FAIL: ожидался SelectorError, получен {type(e).__name__}")
        overall = False

    print("\n" + ("✅ SMOKE PASS" if overall else "❌ SMOKE FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
