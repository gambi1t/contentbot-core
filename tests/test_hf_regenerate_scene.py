"""TDD: ручная пересборка ОДНОЙ сцены HyperFrames (#14, 14 июня).

regenerate_scene(storyboard, scene_id, out_dir): LLM → HTML → HF_PROJECT/
scene_NN.html → рендер одной сцены → out_dir/hyperframes/hf_NN.mp4. Остальные
клипы не трогает. None при сбое LLM/валидации/рендера (старый клип цел).

Запуск: python tests/test_hf_regenerate_scene.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as H  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    storyboard = {"version": "1.0", "scenes": [{"id": f"scene_{i:02d}"} for i in range(1, 7)]}

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        hfproj = td / "hfproj"; hfproj.mkdir()
        out = td / "out"
        (out / "hyperframes").mkdir(parents=True)
        # старые клипы всех 6 сцен
        for i in range(1, 7):
            (out / "hyperframes" / f"hf_{i:02d}.mp4").write_bytes(b"old")

        rendered = {"scene": None}

        def fake_render_one(scene_file, out_dir):
            # эмулируем рендер: создаём новый mp4
            idx = int(scene_file.split("_")[1].split(".")[0])
            p = Path(out_dir) / "hyperframes" / f"hf_{idx:02d}.mp4"
            p.write_bytes(b"NEW")
            rendered["scene"] = scene_file
            return p, None

        with patch.object(H, "HF_PROJECT", hfproj), \
             patch.object(H, "_singleshot_generate_scene",
                          return_value="<!doctype html><html><body>scene</body></html>"), \
             patch.object(H, "_scene_valid_minimal", return_value=(True, [])), \
             patch.object(H, "_render_single_scene", side_effect=fake_render_one):

            print("\n[успешная пересборка scene_04 — только её клип меняется]")
            res = H.regenerate_scene(storyboard, "scene_04", out)
            _assert(res is not None, "вернула путь нового клипа", errors)
            _assert(rendered["scene"] == "scene_04.html", "рендерила ИМЕННО scene_04", errors)
            _assert((hfproj / "scene_04.html").exists(),
                    "новый HTML записан в HF_PROJECT/scene_04.html", errors)
            _assert((out / "hyperframes" / "hf_04.mp4").read_bytes() == b"NEW",
                    "hf_04.mp4 обновлён", errors)
            _assert((out / "hyperframes" / "hf_02.mp4").read_bytes() == b"old",
                    "соседние клипы (hf_02) НЕ тронуты", errors)

        print("\n[LLM упал → None, старый клип цел]")
        (out / "hyperframes" / "hf_03.mp4").write_bytes(b"keep")
        with patch.object(H, "HF_PROJECT", hfproj), \
             patch.object(H, "_singleshot_generate_scene",
                          side_effect=RuntimeError("LLM timeout")):
            res = H.regenerate_scene(storyboard, "scene_03", out)
            _assert(res is None, "None при сбое LLM", errors)
            _assert((out / "hyperframes" / "hf_03.mp4").read_bytes() == b"keep",
                    "старый клип сохранён при сбое", errors)

        print("\n[невалидный HTML → None, не рендерим]")
        with patch.object(H, "HF_PROJECT", hfproj), \
             patch.object(H, "_singleshot_generate_scene", return_value="<bad>"), \
             patch.object(H, "_scene_valid_minimal", return_value=(False, ["нет timeline"])), \
             patch.object(H, "_render_single_scene", side_effect=AssertionError("не должен зваться")):
            res = H.regenerate_scene(storyboard, "scene_05", out)
            _assert(res is None, "None при невалидном HTML (рендер не вызван)", errors)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
