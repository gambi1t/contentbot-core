"""Запуск ТОЛЬКО build-фазы на готовом storyboard.json (без фазы 1).

Зачем: чистый регресс Phase 1 hardening (4 июня). storyboard «Резерв» уже на
сервере, валидирован, ранее дал 2 качественные сцены. Прогоняем все 6 сцен,
получаем суммарное время + scene-by-scene метрики из логов оркестратора.
Зонд rate-limit ДО и ПОСЛЕ — видеть как окно тратится.

Run on server:
  cd /home/maksim-bot/maksim-bot && \\
    sudo -u maksim-bot env CLAUDE_CODE_OAUTH_TOKEN=... HOME=/home/maksim-bot \\
    venv/bin/python tools/run_buildphase_only.py
"""
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hyperframes_broll as H  # noqa: E402


def _probe(label: str):
    """Дешёвый зонд rate-limit (1 turn, ~6с)."""
    env = dict(os.environ)
    tok = env.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        env.pop("ANTHROPIC_API_KEY", None)
    env.setdefault("HOME", str(Path(H.HF_PROJECT).parent))
    try:
        proc = subprocess.run(
            ["claude", "-p", "1+1 одной цифрой",
             "--output-format", "stream-json", "--verbose", "--max-turns", "1"],
            cwd=H.HF_PROJECT, env=env, capture_output=True, text=True, timeout=60,
        )
        diag = H._parse_stream(proc.stdout or "")
        rl = diag.get("rate_limit") or {}
        print(f"[probe:{label}] status={rl.get('status')} "
              f"util={rl.get('utilization')} resets={rl.get('resetsAt')} "
              f"type={rl.get('rateLimitType')}")
        return rl
    except Exception as e:
        print(f"[probe:{label}] FAIL: {e}")
        return None


# ── main ─────────────────────────────────────────────────────────────────
sb_path = H.HF_PROJECT / H.STORYBOARD_FILE
if not sb_path.exists():
    print(f"НЕТ storyboard.json в {H.HF_PROJECT}")
    sys.exit(2)
storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
print(f"=== STORYBOARD ({len(storyboard.get('scenes', []))} сцен) ===")
for s in storyboard.get("scenes", []):
    print(f"  {s.get('id')}: {s.get('business_archetype')} / "
          f"{s.get('hf_technique')} / {s.get('visual_style')}")

_probe("before")

print("\n=== BUILD PHASE ===")
t0 = time.time()
try:
    cost = H._run_build_phase(storyboard)
    outcome = f"OK, cost=${cost:.4f}"
except Exception as e:
    outcome = f"{type(e).__name__}: {e}"
dt = time.time() - t0

print(f"\n=== ИТОГ за {dt:.1f}s ({dt/60:.1f} мин) ===")
print(f"outcome: {outcome}")

# проверка каждой сцены
for i in range(1, 7):
    sf = f"scene_{i:02d}.html"
    p = H.HF_PROJECT / sf
    if p.exists():
        sz = p.stat().st_size
        ok, iss = H._scene_valid_minimal(p, f"scene_{i:02d}")
        print(f"  {sf}: {sz}B, valid={ok}, issues={iss}")
    else:
        print(f"  {sf}: ОТСУТСТВУЕТ")

_probe("after")
