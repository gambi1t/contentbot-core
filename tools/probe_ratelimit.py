"""Дешёвый зонд текущего rate-limit-состояния Max-подписки.
Один тривиальный claude -p (1 turn, без tools) → парсим rate_limit + результат.
Если rejected → печатаем когда сброс. Если result быстро → лимит свободен.
Run: cd /home/maksim-bot/maksim-bot && venv/bin/python tools/probe_ratelimit.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hyperframes_broll as H  # noqa: E402

env = dict(os.environ)
tok = env.get("CLAUDE_CODE_OAUTH_TOKEN")
if tok:
    env.pop("ANTHROPIC_API_KEY", None)
env.setdefault("HOME", str(Path(H.HF_PROJECT).parent))

t0 = time.time()
try:
    proc = subprocess.run(
        ["claude", "-p", "ответь одним словом: ок",
         "--output-format", "stream-json", "--verbose", "--max-turns", "1"],
        cwd=H.HF_PROJECT, env=env, capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout
    rc = proc.returncode
except subprocess.TimeoutExpired as e:
    out = getattr(e, "stdout", "") or ""
    rc = "TIMEOUT(120s)"

dt = time.time() - t0
diag = H._parse_stream(out)
print(f"rc={rc} за {dt:.1f}s; events={diag['num_events']} last={diag['last_type']}")
rl = diag.get("rate_limit")
print(f"rate_limit_info: {rl}")
note = H._rate_limit_note(rl)
print(f"note: {note}")
if diag.get("result_event"):
    r = diag["result_event"]
    print(f"result: subtype={r.get('subtype')} cost={r.get('total_cost_usd')} "
          f"result={(r.get('result') or '')[:80]!r}")
