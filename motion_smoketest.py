"""Python-обёртка над tools/motion_smoketest.mjs для интеграции в оркестратор.

Запускает node-скрипт через subprocess, парсит JSON-output, возвращает Verdict.
Используется в `_run_build_phase` как gate ПОСЛЕ генерации сцены, ДО рендера —
ловит scene_04-style баги (timeline зарегистрирован, но визуально статичен).

Phase 1 Step 5, по ревью ChatGPT 4 июня:
> "MD5 трёх кадров слишком грубый. Делать pixel-diff/perceptual diff по
>  safe-area на 5 точках... Warning не fail, fail — только если timeline есть,
>  но визуального diff почти нет."
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SCRIPT = Path(__file__).resolve().parent / "tools" / "motion_smoketest.mjs"


@dataclass
class MotionVerdict:
    """Итог motion smoke-test одной сцены.

    verdict:
      ok            — motion ≥ STRONG_PCT, явное движение
      warning       — мало (ambient-hold по контракту), но НЕ blocking
      fail          — timeline есть, но визуально статично (как scene_04 seek-bug)
      no_timeline   — нет window.__timelines (композиция битая)
      error         — sanity-check provided html, проверь stderr
    """
    verdict: str
    ok: bool
    max_diff_pct: float | None = None
    diffs: list | None = None
    reason: str | None = None
    timeline_id: str | None = None
    raw: dict | None = None

    @property
    def is_blocking(self) -> bool:
        """Должен ли pipeline остановиться? fail и no_timeline — да; warning — нет."""
        return self.verdict in ("fail", "no_timeline", "error")


def check_motion(scene_html: Path | str, *,
                 browser_path: str | None = None,
                 timeout: int = 60,
                 mode: str = "default") -> MotionVerdict:
    """Запускает motion_smoketest.mjs на сцене и возвращает MotionVerdict.

    mode: "default" | "strict" | "lenient"
    browser_path: путь к chrome-headless-shell для puppeteer, через env.
    """
    if not _SCRIPT.exists():
        return MotionVerdict(
            verdict="error", ok=False,
            reason=f"motion_smoketest.mjs не найден по пути {_SCRIPT}",
        )
    scene_html = Path(scene_html)
    if not scene_html.exists():
        return MotionVerdict(
            verdict="error", ok=False,
            reason=f"сцена не существует: {scene_html}",
        )

    cmd = ["node", str(_SCRIPT), str(scene_html)]
    if mode in ("strict", "lenient"):
        cmd.append(f"--{mode}")

    env = None
    if browser_path:
        import os
        env = dict(os.environ)
        env["HYPERFRAMES_BROWSER_PATH"] = browser_path

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return MotionVerdict(
            verdict="error", ok=False,
            reason=f"motion_smoketest таймаут ({timeout}s)",
        )
    except Exception as e:
        return MotionVerdict(
            verdict="error", ok=False,
            reason=f"motion_smoketest spawn failed: {e}",
        )

    # пытаемся распарсить JSON из stdout (даже если rc != 0)
    raw = None
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return MotionVerdict(
            verdict="error", ok=False,
            reason=f"non-JSON stdout (rc={proc.returncode}): {(proc.stdout or proc.stderr)[:200]}",
        )

    return MotionVerdict(
        verdict=raw.get("verdict", "error"),
        ok=bool(raw.get("ok", False)),
        max_diff_pct=raw.get("max_diff_pct"),
        diffs=raw.get("diffs"),
        reason=raw.get("reason"),
        timeline_id=raw.get("timeline_id"),
        raw=raw,
    )
