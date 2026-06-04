"""Диагностика Phase-1 hardening: один прогон scene_02 (cashflow_timeline) с
новым промптом (без SKILL.md, без Bash, max_turns=8) + capture stream.

Печатает: time-to-finish, tool_counts, num_events, _scene_valid_minimal-итог.
Цель: подтвердить, что убрав SKILL.md-чтение и Bash, scene_02 укладывается и
больше НЕ зацикливается на npx lint. НЕ увеличиваем timeout.

Run on server: cd /home/maksim-bot/maksim-bot && venv/bin/python tools/diag_scene02.py
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                    stream=sys.stdout)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hyperframes_broll as H  # noqa: E402

SCENE_ID = sys.argv[1] if len(sys.argv) > 1 else "scene_02"

sb_path = H.HF_PROJECT / H.STORYBOARD_FILE
if not sb_path.exists():
    print(f"НЕТ storyboard.json в {H.HF_PROJECT} — сначала фаза 1")
    sys.exit(2)
storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
sc = H._scene_contract(storyboard, SCENE_ID)
print(f"=== ДИАГНОСТИКА {SCENE_ID} ===")
print(f"контракт: {sc.get('business_archetype')} / {sc.get('hf_technique')} / "
      f"{sc.get('visual_style')} / {sc.get('motion_family')} / dens={sc.get('density')}")

scene_file = f"{SCENE_ID}.html"
# чистим старый файл, чтобы валидатор не дал ложный успех
p = H.HF_PROJECT / scene_file
if p.exists():
    p.unlink()

prompt = H._build_scene_prompt(storyboard, SCENE_ID, [])
print(f"длина промпта: {len(prompt)} символов")
print(f"промпт содержит 'SKILL.md': {'SKILL.md' in prompt}")
print(f"промпт содержит '#FF5722': {'#FF5722' in prompt}")
print("--- запускаю _run_claude (tools=Read,Edit,Write,Glob,Grep, max_turns=8, "
      "timeout=600) ---")

t0 = time.time()
result = {"outcome": None, "cost": None}
try:
    cost = H._run_claude(prompt, timeout=H.SCENE_BUILD_TIMEOUT,
                         tools="Read,Edit,Write,Glob,Grep", max_turns=8)
    result["outcome"] = "OK"
    result["cost"] = cost
except H.HyperFramesTimeout as e:
    result["outcome"] = f"TIMEOUT: {e}"
except Exception as e:
    result["outcome"] = f"{type(e).__name__}: {e}"
dt = time.time() - t0

print(f"--- завершено за {dt:.1f}s ({dt/60:.1f} мин) ---")
print(f"outcome: {result['outcome']}")
if result["cost"] is not None:
    print(f"cost: ${result['cost']:.4f}")

ok, issues = H._scene_valid_minimal(H.HF_PROJECT / scene_file, SCENE_ID)
print(f"_scene_valid_minimal: ok={ok} issues={issues}")
if (H.HF_PROJECT / scene_file).exists():
    sz = (H.HF_PROJECT / scene_file).stat().st_size
    print(f"{scene_file}: {sz} байт")
