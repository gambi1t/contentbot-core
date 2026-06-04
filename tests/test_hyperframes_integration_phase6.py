"""TDD: Phase 1 Step 6 — финальная интеграция в hyperframes_broll.py.

Интеграционные тесты на новый async-flow:
  - _run_build_phase_async через SceneScheduler (параллель, JobContext-привязка)
  - _render_all_native через tools/render_scene.mjs + ffmpeg
  - motion smoke-test как gate перед render
  - pre-flight rate-limit probe перед батчем

Не запускают реального claude/puppeteer/ffmpeg — мокаем SceneScheduler и
дочерние функции на уровне модуля.

Run: python tests/test_hyperframes_integration_phase6.py
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
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))
import hyperframes_broll as H  # noqa: E402
from job_context import JobContext  # noqa: E402
from scene_scheduler import SceneResult  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _storyboard():
    return {"scenes": [
        {"id": f"scene_{i:02d}", "business_archetype": "hero_number",
         "hf_technique": "x", "visual_style": "y", "motion_family": "z",
         "density": "balanced", "scale_profile": "hero",
         "primary_text": f"text_{i}", "script_beat": "beat", "reason": "r"}
        for i in range(1, 7)
    ]}


# ── 1. _run_build_phase_async: scheduler-based parallel build ────────────
def test_run_build_phase_async_uses_scheduler(errors):
    print("\n-- _run_build_phase_async зовёт SceneScheduler.build_all --")
    _assert(hasattr(H, "_run_build_phase_async"),
            "_run_build_phase_async есть", errors)
    if not hasattr(H, "_run_build_phase_async"):
        return

    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        hf_root = Path(td) / "hf"
        hf_root.mkdir()
        job = JobContext.create("script", runs_root)
        results_returned = {}

        async def fake_build_all(self, prompts, job_, attempt_n=1):
            out = {}
            for sid in prompts:
                # имитируем что scheduler написал html (subagent через Write)
                adir = job_.attempt_dir(sid, attempt_n)
                (adir / f"{sid}.html").write_text("<html>ok</html>", encoding="utf-8")
                out[sid] = SceneResult(
                    scene_id=sid, status="ok", attempt_n=attempt_n,
                    turns=4, cost_usd=0.1, duration_s=5.0,
                    log_path=adir / "stream.jsonl",
                    html_path=adir / f"{sid}.html",
                )
            results_returned["count"] = len(out)
            return out

        from scene_scheduler import SceneScheduler
        with patch.object(SceneScheduler, "build_all", fake_build_all), \
             patch.object(H, "_probe_ratelimit_now",
                          return_value={"status": "allowed", "utilization": 0.1}), \
             patch.object(H, "HF_PROJECT", hf_root), \
             patch.object(H, "_scene_valid_minimal", return_value=(True, [])):
            import asyncio
            cost = asyncio.run(H._run_build_phase_async(_storyboard(), job))

        _assert(results_returned.get("count") == 6, f"6 сцен запрошено (got {results_returned})", errors)
        _assert(abs(cost - 0.6) < 0.001, f"cost суммирован (got {cost})", errors)


def test_run_build_phase_async_promotes_html(errors):
    print("\n-- успешные результаты promoted в HF_PROJECT --")
    if not hasattr(H, "_run_build_phase_async"):
        _assert(False, "_run_build_phase_async есть", errors)
        return

    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        hf_root = Path(td) / "hf"
        hf_root.mkdir()
        job = JobContext.create("script", runs_root)

        # Создаём фейковые html в attempt_dir каждой сцены ДО build_all
        for i in range(1, 7):
            sid = f"scene_{i:02d}"
            adir = job.attempt_dir(sid, 1)
            (adir / f"{sid}.html").write_text(f"<html>scene {i}</html>",
                                              encoding="utf-8")

        async def fake_build_all(self, prompts, job_, attempt_n=1):
            return {sid: SceneResult(
                scene_id=sid, status="ok", attempt_n=attempt_n, turns=4,
                cost_usd=0.1, duration_s=5.0,
                log_path=job_.attempt_dir(sid, attempt_n) / "stream.jsonl",
                html_path=job_.attempt_dir(sid, attempt_n) / f"{sid}.html",
            ) for sid in prompts}

        from scene_scheduler import SceneScheduler
        with patch.object(SceneScheduler, "build_all", fake_build_all), \
             patch.object(H, "_probe_ratelimit_now",
                          return_value={"status": "allowed", "utilization": 0.1}), \
             patch.object(H, "_scene_valid_minimal", return_value=(True, [])), \
             patch.object(H, "HF_PROJECT", hf_root):
            import asyncio
            asyncio.run(H._run_build_phase_async(_storyboard(), job))

        # Все 6 sce должны быть promoted в hf_root/scene_NN.html
        for i in range(1, 7):
            sid = f"scene_{i:02d}"
            promoted = hf_root / f"{sid}.html"
            _assert(promoted.exists(), f"{sid} promoted в HF_PROJECT", errors)


def test_run_build_phase_async_raises_on_failure(errors):
    print("\n-- если все попытки fail → HyperFramesBrollError --")
    if not hasattr(H, "_run_build_phase_async"):
        _assert(False, "_run_build_phase_async есть", errors)
        return

    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("script", runs_root)

        async def fake_build_all(self, prompts, job_, attempt_n=1):
            # все 6 fail — html не пишется
            return {sid: SceneResult(
                scene_id=sid, status="timeout", reason="wall_timeout",
                attempt_n=attempt_n, log_path=None, html_path=None,
            ) for sid in prompts}

        from scene_scheduler import SceneScheduler
        raised = False
        with patch.object(SceneScheduler, "build_all", fake_build_all), \
             patch.object(H, "_probe_ratelimit_now",
                          return_value={"status": "allowed", "utilization": 0.1}), \
             patch.object(H, "_scene_valid_minimal", return_value=(False, ["empty"])):
            import asyncio
            try:
                asyncio.run(H._run_build_phase_async(_storyboard(), job))
            except H.HyperFramesBrollError:
                raised = True
        _assert(raised, "HyperFramesBrollError при полном фейле", errors)


# ── 2. _render_all_native: render через render_scene.mjs + ffmpeg ────────
def test_render_all_native_exists(errors):
    print("\n-- _render_all_native есть --")
    _assert(hasattr(H, "_render_all_native"), "_render_all_native есть", errors)


def test_render_all_native_calls_node_for_each_scene(errors):
    print("\n-- _render_all_native зовёт node tools/render_scene.mjs на каждой сцене --")
    if not hasattr(H, "_render_all_native"):
        _assert(False, "skip — функции нет", errors)
        return

    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "out"
        hf_root = Path(td) / "hf"
        hf_root.mkdir()
        # создаём 6 фейковых scene_NN.html в HF_PROJECT
        for i in range(1, 7):
            (hf_root / f"scene_{i:02d}.html").write_text("<html>x</html>",
                                                         encoding="utf-8")

        calls = []
        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            # имитируем что node делает frames + ffmpeg → out mp4
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            # для ffmpeg-команды — создаём mp4
            if "ffmpeg" in cmd[0] if cmd else "":
                # найти -i argument-1 и output - последний non-option arg
                pass
            # если это ffmpeg — создать выходной файл
            if cmd and "ffmpeg" in str(cmd[0]):
                # последний аргумент = output
                outp = Path(cmd[-1])
                outp.parent.mkdir(parents=True, exist_ok=True)
                outp.write_bytes(b"FAKE MP4")
            return m

        with patch.object(H, "HF_PROJECT", hf_root), \
             patch("subprocess.run", side_effect=fake_run):
            clips, errs = H._render_all_native(out_dir)

        # должно быть как минимум 6 node-вызовов (по одному на сцену)
        node_calls = [c for c in calls if c and "node" in str(c[0])]
        _assert(len(node_calls) == 6, f"6 node-вызовов (got {len(node_calls)})", errors)
        # ffmpeg-вызовов должно быть 6 (по одной склейке на сцену)
        ff_calls = [c for c in calls if c and "ffmpeg" in str(c[0])]
        _assert(len(ff_calls) == 6, f"6 ffmpeg-вызовов (got {len(ff_calls)})", errors)


# ── 3. motion smoke-test as gate ─────────────────────────────────────────
def test_run_motion_gate_passes_ok(errors):
    print("\n-- _run_motion_gate: все сцены ok → не падает --")
    _assert(hasattr(H, "_run_motion_gate"), "_run_motion_gate есть", errors)
    if not hasattr(H, "_run_motion_gate"):
        return

    with tempfile.TemporaryDirectory() as td:
        hf_root = Path(td) / "hf"
        hf_root.mkdir()
        for i in range(1, 7):
            (hf_root / f"scene_{i:02d}.html").write_text("<html>x</html>",
                                                         encoding="utf-8")

        from motion_smoketest import MotionVerdict
        fake_verdict = MotionVerdict(verdict="ok", ok=True, max_diff_pct=0.05)

        with patch.object(H, "HF_PROJECT", hf_root), \
             patch("motion_smoketest.check_motion", return_value=fake_verdict):
            # не должно raise
            H._run_motion_gate()


def test_run_motion_gate_raises_on_blocking(errors):
    print("\n-- _run_motion_gate: fail → HyperFramesBrollError --")
    if not hasattr(H, "_run_motion_gate"):
        return

    with tempfile.TemporaryDirectory() as td:
        hf_root = Path(td) / "hf"
        hf_root.mkdir()
        for i in range(1, 7):
            (hf_root / f"scene_{i:02d}.html").write_text("<html>x</html>",
                                                         encoding="utf-8")

        from motion_smoketest import MotionVerdict
        # одна сцена fail
        verdicts = [MotionVerdict(verdict="ok", ok=True, max_diff_pct=0.05)] * 3 + \
                   [MotionVerdict(verdict="fail", ok=False, reason="static")] + \
                   [MotionVerdict(verdict="ok", ok=True, max_diff_pct=0.05)] * 2

        with patch.object(H, "HF_PROJECT", hf_root), \
             patch("motion_smoketest.check_motion", side_effect=verdicts):
            raised = False
            try:
                H._run_motion_gate()
            except H.HyperFramesBrollError:
                raised = True
            _assert(raised, "fail в одной сцене → HyperFramesBrollError", errors)


def test_run_motion_gate_allows_warning(errors):
    print("\n-- _run_motion_gate: warning не блокирует --")
    if not hasattr(H, "_run_motion_gate"):
        return

    with tempfile.TemporaryDirectory() as td:
        hf_root = Path(td) / "hf"
        hf_root.mkdir()
        for i in range(1, 7):
            (hf_root / f"scene_{i:02d}.html").write_text("<html>x</html>",
                                                         encoding="utf-8")

        from motion_smoketest import MotionVerdict
        # все 6 warning
        warn = MotionVerdict(verdict="warning", ok=True, max_diff_pct=0.005)

        with patch.object(H, "HF_PROJECT", hf_root), \
             patch("motion_smoketest.check_motion", return_value=warn):
            # warning НЕ должен raise
            H._run_motion_gate()


# ── 4. pre-flight rate-limit probe ───────────────────────────────────────
def test_check_ratelimit_high_util_lowers_concurrency(errors):
    print("\n-- _check_ratelimit_before_batch: util>0.7 → concurrency=1 --")
    _assert(hasattr(H, "_check_ratelimit_before_batch"),
            "_check_ratelimit_before_batch есть", errors)
    if not hasattr(H, "_check_ratelimit_before_batch"):
        return

    # mock _parse_stream возвращает rate_limit_info с utilization 0.85
    def fake_probe():
        return {"status": "allowed_warning", "utilization": 0.85,
                "rateLimitType": "five_hour"}

    with patch.object(H, "_probe_ratelimit_now", return_value=fake_probe()):
        c = H._check_ratelimit_before_batch(default_concurrency=2)
        _assert(c == 1, f"util=0.85 → concurrency=1 (got {c})", errors)


def test_check_ratelimit_rejected_raises(errors):
    print("\n-- _check_ratelimit_before_batch: status=rejected → raise --")
    if not hasattr(H, "_check_ratelimit_before_batch"):
        return

    with patch.object(H, "_probe_ratelimit_now",
                     return_value={"status": "rejected", "resetsAt": 1717000000,
                                  "rateLimitType": "five_hour"}):
        raised = False
        try:
            H._check_ratelimit_before_batch(default_concurrency=2)
        except H.HyperFramesBrollError:
            raised = True
        _assert(raised, "rejected → HyperFramesBrollError", errors)


def test_check_ratelimit_allowed_keeps_default(errors):
    print("\n-- _check_ratelimit_before_batch: allowed → default concurrency --")
    if not hasattr(H, "_check_ratelimit_before_batch"):
        return

    with patch.object(H, "_probe_ratelimit_now",
                     return_value={"status": "allowed", "utilization": 0.3}):
        c = H._check_ratelimit_before_batch(default_concurrency=2)
        _assert(c == 2, f"util=0.3 → concurrency=2 (got {c})", errors)


# ── 5. env-flags для legacy fallback ─────────────────────────────────────
def test_env_legacy_build_falls_back_to_sync(errors):
    print("\n-- HF_LEGACY_BUILD=1 → старый _run_build_phase, не async --")
    if not hasattr(H, "_run_build_phase"):
        _assert(False, "_run_build_phase должен существовать (legacy)", errors)
        return
    # просто проверка что функция существует и принимает storyboard
    import inspect
    sig = inspect.signature(H._run_build_phase)
    # signature должен принимать storyboard (как раньше)
    _assert("storyboard" in sig.parameters,
            f"_run_build_phase(storyboard, ...) — legacy совместим (params: {list(sig.parameters)})",
            errors)


def main():
    print("=" * 60)
    print("test_hyperframes_integration_phase6")
    print("=" * 60)
    errors = []
    test_run_build_phase_async_uses_scheduler(errors)
    test_run_build_phase_async_promotes_html(errors)
    test_run_build_phase_async_raises_on_failure(errors)
    test_render_all_native_exists(errors)
    test_render_all_native_calls_node_for_each_scene(errors)
    test_run_motion_gate_passes_ok(errors)
    test_run_motion_gate_raises_on_blocking(errors)
    test_run_motion_gate_allows_warning(errors)
    test_check_ratelimit_high_util_lowers_concurrency(errors)
    test_check_ratelimit_rejected_raises(errors)
    test_check_ratelimit_allowed_keeps_default(errors)
    test_env_legacy_build_falls_back_to_sync(errors)
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
