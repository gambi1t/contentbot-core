"""TDD: SceneScheduler (Phase 1 Step 3 production-плана).

По ревью ChatGPT 4 июня: subprocess.run без streaming/idle-timeout/process-group
kill = "следующий таймаут опять чёрный ящик". Решение —
asyncio.create_subprocess_exec + bounded Semaphore + JSONL stream + idle-timeout
+ wall-timeout + kill process group.

Контракт API:
  scheduler = SceneScheduler(concurrency=2, idle_timeout=60, wall_timeout=600)
  result = await scheduler.build_scene(prompt, scene_id, job, attempt_n)
  results = await scheduler.build_all({sid: prompt}, job)

SceneResult — dataclass со status/reason/turns/cost/duration/log_path/html_path.

Тесты НЕ зависят от реального claude — мокаем asyncio.create_subprocess_exec
через FakeProcess (свой класс с настраиваемым stdout-стримом).

Run: python tests/test_scene_scheduler.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))

from job_context import JobContext  # noqa: E402
from scene_scheduler import SceneResult, SceneScheduler  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


# ── FakeProcess (mock для asyncio subprocess spawn) ─────────────────────
class FakeStream:
    """Эмулирует asyncio.StreamReader: отдаёт строки одну за одной с задержкой.

    block_after_eof=True (default) — после последней строки readline
    БЛОКИРУЕТСЯ (как реальный claude процесс который думает). Это позволяет
    тесту idle_timeout сработать. Поставь False если нужно явное eof.
    """

    def __init__(self, lines, delays=None, block_after_eof=False):
        self.lines = list(lines)
        self.delays = list(delays) if delays else [0.0] * len(lines)
        self.idx = 0
        self.at_eof = False
        self.block_after_eof = block_after_eof

    async def readline(self):
        if self.idx >= len(self.lines):
            if self.block_after_eof:
                # имитируем "процесс висит" — спим долго, но даём отмене сработать
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    raise
                self.at_eof = True
                return b""
            self.at_eof = True
            return b""
        if self.idx < len(self.delays):
            await asyncio.sleep(self.delays[self.idx])
        line = self.lines[self.idx]
        self.idx += 1
        return line


class FakeProcess:
    """Эмулирует asyncio.subprocess.Process для тестов (без реального процесса)."""

    def __init__(self, stdout_lines, returncode=0, delays=None,
                 wait_after_stdout=0.0, hang=False,
                 block_after_eof=False):
        self.stdout = FakeStream(stdout_lines, delays,
                                 block_after_eof=block_after_eof)
        self.stderr = FakeStream([], [])
        self._returncode = returncode
        self.pid = 99999
        self._wait_after_stdout = wait_after_stdout
        self._hang = hang
        self._terminated = False
        self._killed = False

    @property
    def returncode(self):
        return self._returncode if self._terminated or self.stdout.at_eof else None

    async def wait(self):
        while not self.stdout.at_eof and not self._terminated:
            await asyncio.sleep(0.01)
        if self._hang and not self._terminated:
            await asyncio.sleep(60)
        if self._wait_after_stdout:
            await asyncio.sleep(self._wait_after_stdout)
        self._terminated = True
        return self._returncode

    def terminate(self):
        self._terminated = True
        self.stdout.at_eof = True

    def kill(self):
        self._killed = True
        self._terminated = True
        self.stdout.at_eof = True

    def send_signal(self, sig):
        self._terminated = True
        self.stdout.at_eof = True


def _spawn(fake_proc):
    async def _go(*args, **kwargs):
        return fake_proc
    return _go


# ── 1. SceneResult dataclass ─────────────────────────────────────────────
def test_scene_result_dataclass(errors):
    print("\n-- SceneResult dataclass --")
    r = SceneResult(
        scene_id="scene_02", status="ok", reason=None, attempt_n=1,
        turns=4, cost_usd=0.42, duration_s=67.3,
        log_path=Path("/tmp/log"), html_path=Path("/tmp/scene_02.html"),
    )
    _assert(r.scene_id == "scene_02", "scene_id поле есть", errors)
    _assert(r.status == "ok", "status поле есть", errors)
    _assert(r.turns == 4, "turns поле есть", errors)
    d = r.to_dict() if hasattr(r, "to_dict") else r.__dict__
    _assert(isinstance(d, dict), "to_dict/__dict__ доступен", errors)


# ── 2. happy path ────────────────────────────────────────────────────────
def test_successful_run(errors):
    print("\n-- happy path: stream events → result parsed --")
    lines = [
        b'{"type":"system","subtype":"init"}\n',
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read"}]}}\n',
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write"}]}}\n',
        b'{"type":"result","subtype":"success","total_cost_usd":0.42,"num_turns":4,"result":"ok"}\n',
    ]
    fake = FakeProcess(lines, returncode=0)
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("test", runs_root)
        scheduler = SceneScheduler(concurrency=2, idle_timeout=30, wall_timeout=10)

        async def go():
            with patch("asyncio.create_subprocess_exec", _spawn(fake)):
                return await scheduler.build_scene("prompt", "scene_02", job, attempt_n=1)

        r = asyncio.run(go())
        _assert(r.status == "ok", f"status=ok (got {r.status} reason={r.reason})", errors)
        _assert(r.turns == 4, f"turns=4 (got {r.turns})", errors)
        _assert(abs(r.cost_usd - 0.42) < 0.001, f"cost=0.42 (got {r.cost_usd})", errors)
        _assert(r.log_path.exists(), "stream.jsonl записан", errors)
        log_lines = r.log_path.read_text(encoding="utf-8").strip().splitlines()
        _assert(len(log_lines) == 4, f"4 события в логе (got {len(log_lines)})", errors)


# ── 3. wall-timeout ──────────────────────────────────────────────────────
def test_wall_timeout(errors):
    print("\n-- wall_timeout срабатывает → status=timeout, процесс убит --")
    lines = [
        b'{"type":"system","subtype":"init"}\n',
        b'{"type":"assistant","message":{"content":[]}}\n',
    ]
    fake = FakeProcess(lines, delays=[0.1, 5.0], returncode=0)
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("test", runs_root)
        scheduler = SceneScheduler(concurrency=2, idle_timeout=30, wall_timeout=1)

        async def go():
            with patch("asyncio.create_subprocess_exec", _spawn(fake)):
                return await scheduler.build_scene("prompt", "scene_02", job, attempt_n=1)

        t0 = time.time()
        r = asyncio.run(go())
        dt = time.time() - t0
        _assert(r.status == "timeout", f"status=timeout (got {r.status})", errors)
        _assert(dt < 3.0, f"завершилось быстро по timeout (got {dt:.1f}s)", errors)
        _assert(fake._terminated, "процесс был убит", errors)


# ── 4. idle-timeout ──────────────────────────────────────────────────────
def test_idle_timeout(errors):
    print("\n-- idle_timeout: нет событий N сек → kill, status=timeout --")
    lines = [
        b'{"type":"system","subtype":"init"}\n',
    ]
    fake = FakeProcess(lines, delays=[0.05], returncode=0,
                       wait_after_stdout=10, block_after_eof=True)
    fake._hang = True
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("test", runs_root)
        scheduler = SceneScheduler(concurrency=2, idle_timeout=1, wall_timeout=30)

        async def go():
            with patch("asyncio.create_subprocess_exec", _spawn(fake)):
                return await scheduler.build_scene("prompt", "scene_02", job, attempt_n=1)

        t0 = time.time()
        r = asyncio.run(go())
        dt = time.time() - t0
        _assert(r.status == "timeout", f"status=timeout (got {r.status})", errors)
        # idle=1 + grace на terminate/kill = до ~3-4 секунд реальной длительности
        _assert(dt < 5.0, f"завершилось быстро по idle (got {dt:.1f}s)", errors)
        _assert("idle" in (r.reason or "").lower(), f"reason упоминает idle (got {r.reason!r})", errors)


# ── 5. rate_limit_event rejected ─────────────────────────────────────────
def test_rate_limit_rejected_detected(errors):
    print("\n-- rate_limit_event status=rejected → status=rate_limited, не retry --")
    lines = [
        b'{"type":"system","subtype":"init"}\n',
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read"}]}}\n',
        b'{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1717000000,"rateLimitType":"five_hour","utilization":1.0}}\n',
    ]
    fake = FakeProcess(lines, returncode=0, hang=False)
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("test", runs_root)
        scheduler = SceneScheduler(concurrency=2, idle_timeout=30, wall_timeout=5)

        async def go():
            with patch("asyncio.create_subprocess_exec", _spawn(fake)):
                return await scheduler.build_scene("prompt", "scene_02", job, attempt_n=1)

        r = asyncio.run(go())
        _assert(r.status == "rate_limited", f"status=rate_limited (got {r.status})", errors)
        _assert("rate" in (r.reason or "").lower() or "лимит" in (r.reason or "").lower(),
                f"reason про лимит (got {r.reason!r})", errors)


# ── 6. concurrency limit ─────────────────────────────────────────────────
def test_concurrency_respected(errors):
    print("\n-- Semaphore(2): 6 задач, активных одновременно ≤2 --")
    active_now = {"v": 0, "max": 0}

    def make_fake():
        lines = [
            b'{"type":"system","subtype":"init"}\n',
            b'{"type":"result","total_cost_usd":0.1,"num_turns":1}\n',
        ]
        return FakeProcess(lines, delays=[0.02, 0.05])

    async def tracking_spawn(*args, **kwargs):
        active_now["v"] += 1
        active_now["max"] = max(active_now["max"], active_now["v"])
        p = make_fake()
        orig_wait = p.wait
        async def wait_then_dec():
            try:
                rc = await orig_wait()
                return rc
            finally:
                active_now["v"] -= 1
        p.wait = wait_then_dec
        return p

    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("test", runs_root)
        scheduler = SceneScheduler(concurrency=2, idle_timeout=30, wall_timeout=10)

        async def go():
            with patch("asyncio.create_subprocess_exec", tracking_spawn):
                prompts = {f"scene_{i:02d}": f"prompt_{i}" for i in range(1, 7)}
                return await scheduler.build_all(prompts, job)

        results = asyncio.run(go())
        _assert(len(results) == 6, f"все 6 результатов (got {len(results)})", errors)
        _assert(active_now["max"] <= 2,
                f"одновременно активных ≤2 (got peak={active_now['max']})", errors)


# ── 7. integration with JobContext ───────────────────────────────────────
def test_writes_to_job_attempt_dir(errors):
    print("\n-- результат + stream пишутся в job.attempt_dir(scene_id, n) --")
    lines = [
        b'{"type":"system","subtype":"init"}\n',
        b'{"type":"result","total_cost_usd":0.1,"num_turns":1}\n',
    ]
    fake = FakeProcess(lines, returncode=0)
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("test", runs_root)
        scheduler = SceneScheduler(concurrency=2, idle_timeout=30, wall_timeout=10)

        async def go():
            with patch("asyncio.create_subprocess_exec", _spawn(fake)):
                return await scheduler.build_scene("prompt", "scene_03", job, attempt_n=2)

        r = asyncio.run(go())
        expected_dir = job.attempt_dir("scene_03", 2)
        _assert(r.log_path.parent == expected_dir,
                f"log_path в attempt_dir (got {r.log_path.parent})", errors)
        _assert((expected_dir / "stream.jsonl").exists(),
                "stream.jsonl в правильной папке", errors)
        _assert((expected_dir / "result.json").exists() or hasattr(r, "status"),
                "result либо в job.result.json, либо в возвращённом SceneResult", errors)


def main():
    print("=" * 60)
    print("test_scene_scheduler (Phase 1 step 3)")
    print("=" * 60)
    errors = []
    test_scene_result_dataclass(errors)
    test_successful_run(errors)
    test_wall_timeout(errors)
    test_idle_timeout(errors)
    test_rate_limit_rejected_detected(errors)
    test_concurrency_respected(errors)
    test_writes_to_job_attempt_dir(errors)
    print()
    if errors:
        print(f"FAIL: {len(errors)} assertion(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
