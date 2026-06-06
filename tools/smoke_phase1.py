"""Phase 1 production smoke-test.

Полный прогон generate_hyperframes_broll по тестовому сценарию. Проверяет:
- _run_storyboard_phase (вчерашний код)
- _check_ratelimit_before_batch (новый Step 6)
- _run_build_phase_async через SceneScheduler (Step 3+6)
- workspace в runs/<job>/ (Step 1)
- atomic promote сцен в HF_PROJECT (Step 1)
- _run_motion_gate (Step 5+6)
- _inspect_all_scenes (вчерашний layout-детектор)
- _render_all_native через render_scene.mjs + ffmpeg (Step 4+6)

Печатает по этапам что происходит. P50 ~4-6 мин.

Run: cd /home/maksim-bot/maksim-bot && \\
       sudo -u maksim-bot env CLAUDE_CODE_OAUTH_TOKEN=... HOME=/home/maksim-bot \\
       venv/bin/python tools/smoke_phase1.py
"""
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SCRIPT = """\
В сезонном бизнесе расходы идут весь год: аренда, зарплаты, обслуживание.
А выручка только летом. Без резерва ты к сентябрю обнуляешься и принимаешь
решения из страха.

Формула простая: возьми три самых тяжёлых месяца расходов и умножь на 1.5.
Это твоя финансовая подушка. Когда она собрана, ты перестаёшь думать о
выживании и начинаешь думать на годы вперёд.

Сядь сегодня вечером и посчитай — сколько твоих месячных расходов лежит
у тебя в резерве прямо сейчас.
"""

OUT = Path("/tmp/hf_smoke_phase1")
OUT.mkdir(parents=True, exist_ok=True)
print(f"=== SMOKE Phase 1 ===")
print(f"out_dir: {OUT}")
print(f"script: {len(SCRIPT)} chars")
print(f"env HF_BUILD_CONCURRENCY={os.getenv('HF_BUILD_CONCURRENCY', '2 (default)')}")
print(f"env HF_LEGACY_BUILD={os.getenv('HF_LEGACY_BUILD', '(not set, новый flow)')}")
print()

from hyperframes_broll import generate_hyperframes_broll  # noqa: E402

t0 = time.time()
try:
    clips, cost = generate_hyperframes_broll(SCRIPT, OUT)
    dt = time.time() - t0
    print()
    print(f"=== УСПЕХ за {dt:.1f}s ({dt/60:.1f} мин) ===")
    print(f"clips: {len(clips)}")
    for c in clips:
        sz = c.stat().st_size if c.exists() else 0
        print(f"  {c.name}: {sz} байт ({sz/1024:.0f} KB)")
    print(f"total cost: ${cost:.4f}")
    sys.exit(0)
except Exception as e:
    dt = time.time() - t0
    print()
    print(f"=== FAIL за {dt:.1f}s ===")
    print(f"{type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
