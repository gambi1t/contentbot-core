"""SceneScheduler — производственный async-планировщик per-scene build.

Phase 1 Step 3 production-плана (по ревью ChatGPT 4 июня 2026):
- asyncio.create_subprocess_exec вместо subprocess.run → можно стримить stdout
  построчно в JSONL realtime, не дожидаясь завершения.
- asyncio.Semaphore(concurrency) → bounded параллель (default 2 по ревью GPT,
  не 6 — OOM-риск на 7.6Gi сервере).
- idle-timeout: если N секунд нет событий из stdout — kill (зависший агент).
- wall-timeout: общий потолок на сцену (текущий SCENE_BUILD_TIMEOUT=600).
- rate-limit-aware: при rate_limit_event {status:"rejected"} — exit early с
  status="rate_limited", чтобы оркестратор НЕ retry-ил в ту же стену.
- process-group kill (best-effort, кросс-платформенно): сначала terminate,
  если за grace не вышло — kill.
- Интеграция с JobContext (Step 1): пишет stream.jsonl + result.json в
  attempt_dir, валидный HTML потом promote в HF_PROJECT.

Тесты замоканы через FakeProcess — не зависят от реального claude и платформы.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# event-types из stream-json (CLI claude --output-format stream-json --verbose)
_RESULT = "result"
_ASSISTANT = "assistant"
_RATE_LIMIT = "rate_limit_event"

# при terminate ждём столько до hard-kill
_KILL_GRACE_S = 1.0


# ── SceneResult ──────────────────────────────────────────────────────────
@dataclass
class SceneResult:
    """Итог одной попытки сборки сцены.

    status:
      ok            — успешно записан и валиден (caller потом promote)
      timeout       — wall- или idle-timeout, процесс убит
      rate_limited  — rate_limit_event с status=rejected, retry бессмысленен
      killed        — принудительно прерван caller'ом (cancel)
      error         — процесс упал (rc != 0) или crash в парсере
    """
    scene_id: str
    status: str
    reason: str | None = None
    attempt_n: int = 1
    turns: int | None = None
    cost_usd: float = 0.0
    duration_s: float = 0.0
    log_path: Path | None = None
    html_path: Path | None = None
    rate_limit_info: dict | None = None  # последний rate_limit_info из стрима

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("log_path", "html_path"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        return d


# ── Парсер одного события стрима ─────────────────────────────────────────
def _parse_event(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


def _rate_limit_rejected(info: dict | None) -> bool:
    return isinstance(info, dict) and info.get("status") == "rejected"


def _format_rate_limit_reason(info: dict | None) -> str:
    if not isinstance(info, dict):
        return "rate-limit"
    parts = ["лимит API исчерпан"]
    rl_type = info.get("rateLimitType")
    if rl_type:
        parts.append(f"тип: {rl_type}")
    resets = info.get("resetsAt")
    if isinstance(resets, (int, float)) and resets > 0:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(resets))
            parts.append(f"сброс ~{ts}")
        except Exception:
            parts.append(f"сброс epoch={int(resets)}")
    return "; ".join(parts)


# ── kill best-effort (cross-platform) ────────────────────────────────────
async def _terminate_and_kill(proc) -> None:
    """Сначала terminate (SIGTERM/CTRL_BREAK), потом kill если не вышел.

    На Windows SIGTERM мапится на TerminateProcess. process-group reliable kill
    требует psutil — здесь оставляем best-effort, для production можно
    подтянуть psutil позже.
    """
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except Exception as e:
        logger.warning(f"[scheduler] terminate failed: {e}")
    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_S)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception as e:
            logger.warning(f"[scheduler] kill failed: {e}")
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_S)
        except asyncio.TimeoutError:
            pass


# ── SceneScheduler ───────────────────────────────────────────────────────
class SceneScheduler:
    """Параллельный async-планировщик per-scene build."""

    def __init__(self, concurrency: int = 2, idle_timeout: float = 60.0,
                 wall_timeout: float = 600.0,
                 cli_args_builder=None,
                 env_factory=None):
        self.concurrency = max(1, int(concurrency))
        self.idle_timeout = float(idle_timeout)
        self.wall_timeout = float(wall_timeout)
        self.cli_args_builder = cli_args_builder or self._default_cli_args
        self.env_factory = env_factory or (lambda: dict(os.environ))
        self._sem = asyncio.Semaphore(self.concurrency)

    @staticmethod
    def _default_cli_args(prompt: str, attempt_dir: Path) -> list[str]:
        # claude -p <prompt> --output-format stream-json --verbose
        #   --allowedTools Read,Edit,Write,Glob,Grep,Bash --max-turns 16
        # (max_turns=16 + Bash вернули по доказанным 4 июня инсайтам)
        return [
            "claude", "-p", prompt,
            "--output-format", "stream-json", "--verbose",
            "--allowedTools", "Read,Edit,Write,Glob,Grep,Bash",
            "--max-turns", "16",
        ]

    # ── один build_scene ─────────────────────────────────────────────────
    async def build_scene(self, prompt: str, scene_id: str,
                          job, attempt_n: int = 1) -> SceneResult:
        async with self._sem:
            return await self._do_build(prompt, scene_id, job, attempt_n)

    async def _do_build(self, prompt: str, scene_id: str,
                        job, attempt_n: int) -> SceneResult:
        adir = job.attempt_dir(scene_id, attempt_n)
        log_path = adir / "stream.jsonl"

        t0 = time.time()
        cmd = self.cli_args_builder(prompt, adir)
        env = self.env_factory()

        # spawn — мокается в тестах через patch на asyncio.create_subprocess_exec
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env,
            )
        except Exception as e:
            return SceneResult(
                scene_id=scene_id, status="error", reason=f"spawn failed: {e}",
                attempt_n=attempt_n, duration_s=time.time() - t0,
                log_path=log_path,
            )

        last_event_at = time.time()
        wall_deadline = t0 + self.wall_timeout
        rate_limit_info: dict | None = None
        result_event: dict | None = None
        tool_counts: dict[str, int] = {}
        events_count = 0
        early_exit_reason: str | None = None
        early_exit_status: str | None = None

        # открываем stream.jsonl для realtime-записи (line-buffered)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = log_path.open("w", encoding="utf-8", buffering=1)

        try:
            while True:
                idle_remaining = self.idle_timeout - (time.time() - last_event_at)
                wall_remaining = wall_deadline - time.time()
                wait_for = max(0.05, min(idle_remaining, wall_remaining))
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(),
                                                 timeout=wait_for)
                except asyncio.TimeoutError:
                    now = time.time()
                    if now - last_event_at >= self.idle_timeout:
                        early_exit_status = "timeout"
                        early_exit_reason = (
                            f"idle > {self.idle_timeout:.0f}s без событий "
                            f"(events={events_count}, "
                            f"tools={dict(tool_counts) or '—'})"
                        )
                        break
                    if now >= wall_deadline:
                        early_exit_status = "timeout"
                        early_exit_reason = (
                            f"wall > {self.wall_timeout:.0f}s "
                            f"(events={events_count})"
                        )
                        break
                    continue

                if not raw:
                    break

                try:
                    line = raw.decode("utf-8", errors="replace")
                except Exception:
                    line = ""
                log_fh.write(line if line.endswith("\n") else line + "\n")

                event = _parse_event(line)
                if event is None:
                    continue
                events_count += 1
                last_event_at = time.time()
                etype = event.get("type")

                if etype == _RESULT:
                    result_event = event
                elif etype == _RATE_LIMIT:
                    info = event.get("rate_limit_info")
                    if isinstance(info, dict):
                        rate_limit_info = info
                        if _rate_limit_rejected(info):
                            early_exit_status = "rate_limited"
                            early_exit_reason = _format_rate_limit_reason(info)
                            break
                elif etype == _ASSISTANT:
                    msg = event.get("message")
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_use":
                                name = b.get("name") or "?"
                                tool_counts[name] = tool_counts.get(name, 0) + 1
        finally:
            try:
                log_fh.close()
            except Exception:
                pass

        if early_exit_status is not None:
            await _terminate_and_kill(proc)
        else:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                await _terminate_and_kill(proc)

        duration = time.time() - t0

        status = early_exit_status
        reason = early_exit_reason
        turns = None
        cost = 0.0

        if status is None:
            if result_event:
                raw_cost = result_event.get("total_cost_usd", 0.0)
                try:
                    cost = float(raw_cost) if raw_cost is not None else 0.0
                except (TypeError, ValueError):
                    cost = 0.0
                turns = result_event.get("num_turns")
                rc = proc.returncode
                if rc == 0 or rc is None:
                    status = "ok"
                else:
                    status = "error"
                    reason = f"rc={rc}"
            else:
                rc = proc.returncode
                status = "error" if (rc not in (0, None)) else "error"
                reason = (reason or f"нет type=result, rc={rc}, "
                          f"events={events_count}")

        html_path = adir / f"{scene_id}.html"
        if not html_path.exists():
            html_path = None

        result = SceneResult(
            scene_id=scene_id, status=status, reason=reason,
            attempt_n=attempt_n, turns=turns, cost_usd=cost,
            duration_s=duration, log_path=log_path, html_path=html_path,
            rate_limit_info=rate_limit_info,
        )
        try:
            job.record_attempt(scene_id, attempt_n, result.to_dict())
        except Exception as e:
            logger.warning(f"[scheduler] record_attempt failed: {e}")
        return result

    # ── параллельная сборка всех ────────────────────────────────────────
    async def build_all(self, prompts: dict[str, str], job,
                        attempt_n: int = 1) -> dict[str, SceneResult]:
        """Параллельный build всех сцен — ограничен concurrency через Semaphore.

        Возвращает dict {scene_id: SceneResult}. Если task падает с
        exception — оборачивается в SceneResult(status='error').
        """
        async def _one(sid: str, p: str):
            try:
                return await self.build_scene(p, sid, job, attempt_n)
            except Exception as e:
                return SceneResult(
                    scene_id=sid, status="error", reason=f"task crash: {e}",
                    attempt_n=attempt_n, log_path=None,
                )

        tasks = [_one(sid, p) for sid, p in prompts.items()]
        results = await asyncio.gather(*tasks)
        return {r.scene_id: r for r in results}
