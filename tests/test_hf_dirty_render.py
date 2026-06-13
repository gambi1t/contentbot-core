"""TDD: dirty-рендер HyperFrames (13 июня).

_render_all_native рендерит только сцены с изменившимся HTML; неизменённые
берёт из готового mp4 (кэш по html-hash + наличие mp4). fix-round правит
1-2 сцены из 6 — остальные не должны ре-рендериться (рендер 1 сцены ~77с).

Запуск: python tests/test_hf_dirty_render.py
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
    scenes = ["scene_01.html", "scene_02.html", "scene_03.html"]

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        hfproj = td / "hfproj"; hfproj.mkdir()
        outdir = td / "out"
        for sf in scenes:
            (hfproj / sf).write_text(f"<html>{sf} v1</html>", encoding="utf-8")

        rendered: list[str] = []

        def fake_run(cmd, **kw):
            m = MagicMock(); m.returncode = 0; m.stdout = ""; m.stderr = ""
            if cmd[0] == "node":               # render_scene.mjs <scene> <frames> 5 30
                rendered.append(Path(cmd[2]).name)
            elif cmd[0] == "ffmpeg":           # ...последний арг = out_mp4
                Path(cmd[-1]).write_bytes(b"fake-mp4")
            return m

        with patch.object(H, "HF_PROJECT", hfproj), \
             patch.object(H, "SCENE_FILES", scenes), \
             patch.object(H.subprocess, "run", side_effect=fake_run):

            print("\n[1-й прогон — рендерятся ВСЕ сцены]")
            clips1, err1 = H._render_all_native(outdir)
            _assert(not err1, "без ошибок", errors)
            _assert(len(clips1) == 3, "3 клипа на выходе", errors)
            _assert(sorted(rendered) == scenes, "отрендерены все 3 сцены", errors)
            _assert((outdir / "hyperframes" / ".render_cache.json").exists(),
                    "кэш-манифест записан", errors)

            print("\n[2-й прогон — изменена только scene_02 (как fix-round)]")
            rendered.clear()
            (hfproj / "scene_02.html").write_text("<html>scene_02 FIXED</html>",
                                                  encoding="utf-8")
            clips2, err2 = H._render_all_native(outdir)
            _assert(not err2, "без ошибок", errors)
            _assert(len(clips2) == 3, "3 клипа (2 из кэша + 1 свежий)", errors)
            _assert(rendered == ["scene_02.html"],
                    "ре-рендерена ТОЛЬКО изменённая сцена", errors)

            print("\n[3-й прогон — ничего не менялось → 0 рендеров]")
            rendered.clear()
            clips3, err3 = H._render_all_native(outdir)
            _assert(rendered == [], "ни одной сцены не ре-рендерено", errors)
            _assert(len(clips3) == 3, "все 3 клипа из кэша", errors)

            print("\n[guard: mp4 удалён, hash тот же → всё равно рендерим]")
            rendered.clear()
            (outdir / "hyperframes" / "hf_01.mp4").unlink()
            H._render_all_native(outdir)
            _assert(rendered == ["scene_01.html"],
                    "удалённый mp4 → сцена рендерится несмотря на кэш", errors)

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
