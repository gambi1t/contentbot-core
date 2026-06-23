"""A (HyperFrames масштаб под длину): число сцен считается по длине озвучки
(через число слов), а не жёстко 6. Раньше 6 сцен под ~30с → на 50-60с не хватало
материала, монтаж дублировал клипы (Артём 22.06). Чистые хелперы.

Run: python tests/test_hf_scene_count.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as hf  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _words(n: int) -> str:
    return " ".join(["слово"] * n)


def test_scales_with_length(errors):
    print("\n-- scene_count растёт с длиной (≈ длина озвучки) --")
    n30 = hf._scene_count_for_script(_words(69))    # ~30с
    n50 = hf._scene_count_for_script(_words(115))   # ~50с (тот самый кейс Артёма)
    n60 = hf._scene_count_for_script(_words(138))   # ~60с
    _assert(n50 > n30, f"50с ({n50}) > 30с ({n30}) — больше материала", errors)
    _assert(n50 == 8, f"~50с → 8 сцен (было 6), got {n50}", errors)
    _assert(n60 >= n50, f"60с ({n60}) ≥ 50с ({n50})", errors)


def test_clamp_bounds(errors):
    print("\n-- clamp [HF_MIN_SCENES..HF_MAX_SCENES] --")
    _assert(hf._scene_count_for_script(_words(3)) == hf.HF_MIN_SCENES, f"короткий → пол {hf.HF_MIN_SCENES}", errors)
    _assert(hf._scene_count_for_script(_words(300)) == hf.HF_MAX_SCENES, f"длинный → потолок {hf.HF_MAX_SCENES}", errors)
    for n in (1, 50, 100, 200, 500):
        c = hf._scene_count_for_script(_words(n))
        _assert(hf.HF_MIN_SCENES <= c <= hf.HF_MAX_SCENES, f"{n} слов → {c} в [{hf.HF_MIN_SCENES},{hf.HF_MAX_SCENES}]", errors)


def test_empty_default(errors):
    print("\n-- пустой/None → дефолт 6 (как раньше) --")
    _assert(hf._scene_count_for_script("") == 6, "'' → 6", errors)
    _assert(hf._scene_count_for_script(None) == 6, "None → 6", errors)


def test_scene_files_for(errors):
    print("\n-- _scene_files_for(n) → scene_01..scene_NN.html --")
    f = hf._scene_files_for(8)
    _assert(len(f) == 8, f"8 файлов, got {len(f)}", errors)
    _assert(f[0] == "scene_01.html" and f[-1] == "scene_08.html", f"нумерация: {f[0]}..{f[-1]}", errors)
    _assert(hf._scene_files_for(5) == [f"scene_{i:02d}.html" for i in range(1, 6)], "5 сцен корректно", errors)


def main() -> int:
    print("=" * 60 + "\nHyperFrames scene-count scaling (A)\n" + "=" * 60)
    errors: list = []
    for fn in (test_scales_with_length, test_clamp_bounds, test_empty_default, test_scene_files_for):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all hf-scene-count tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
