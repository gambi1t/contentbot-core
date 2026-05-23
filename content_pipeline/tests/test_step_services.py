"""Unit tests for the headless BotStepRunner — with a fake Claude, no network.

These prove the real step runner satisfies the StepRunner contract and parses
model output the same way bot.py does, without importing bot.py.

Run: python -m unittest content_pipeline.tests.test_step_services -v
"""
from __future__ import annotations

import unittest

from pipeline_step_services import BotStepRunner
from content_pipeline.store import PipelineStore
from content_pipeline.steps import MockStepRunner  # sanity: protocol shape
from content_pipeline.core import PipelineSpine, EffectExecutor, drive
from content_pipeline.models import (
    PipelineEvent, EV_IDEA_RECEIVED, EV_APPROVE,
    UI_SHOW_STEP, STAGE_COVER,
)


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeClaude:
    """Returns canned text per call; records calls for assertions."""
    def __init__(self, script_text="Готовый сценарий про картинг", cover_text=None):
        self.script_text = script_text
        self.cover_text = cover_text or (
            "Один заезд меняет всё\n"
            "Почему утро решает день\n"
            "Трасса пустая в 9 утра\n"
            "x\n"                              # too short → filtered out
            "Скорость которая отрезвляет"
        )
        self.calls = []

        class _Messages:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kw):
                self._outer.calls.append(kw)
                # crude: cover prompt mentions "обложк", script otherwise
                user = kw["messages"][0]["content"].lower()
                if "обложк" in user:
                    return _FakeResp(self._outer.cover_text)
                return _FakeResp(self._outer.script_text)

        self.messages = _Messages(self)


def _runner(fake):
    return BotStepRunner(
        fake,
        script_system_fn=lambda: "SCRIPT SYSTEM",
        cover_system_fn=lambda: "COVER SYSTEM",
    )


class TestBotStepRunner(unittest.TestCase):
    def test_generate_script_returns_text(self):
        fake = _FakeClaude(script_text="СЦЕНАРИЙ:\nВот тело сценария")
        runner = _runner(fake)
        res = runner.generate_script("r1", "идея про картинг", {})
        # leading "СЦЕНАРИЙ:" label is stripped, like bot.py
        self.assertEqual(res["text_content"], "Вот тело сценария")
        self.assertEqual(fake.calls[0]["system"], "SCRIPT SYSTEM")

    def test_force_shorten_applied(self):
        fake = _FakeClaude(script_text="длинный " * 50)
        runner = BotStepRunner(
            fake,
            script_system_fn=lambda: "S",
            cover_system_fn=lambda: "C",
            force_shorten=lambda t: t[:20],
        )
        res = runner.generate_script("r1", "идея", {})
        self.assertEqual(len(res["text_content"]), 20)

    def test_generate_cover_options_parses_and_filters(self):
        runner = _runner(_FakeClaude())
        res = runner.generate_cover_options("r1", "сценарий", {})
        opts = res["meta"]["options"]
        self.assertTrue(2 <= len(opts) <= 5)
        self.assertNotIn("x", opts)  # too-short line filtered
        self.assertTrue(all(10 <= len(o) <= 50 for o in opts))

    def test_start_paid_job_not_implemented_in_1b(self):
        runner = _runner(_FakeClaude())
        with self.assertRaises(NotImplementedError):
            runner.start_paid_job("r1", "avatar", {})

    def test_drives_spine_with_real_runner_to_cover(self):
        """The real runner plugs into the spine exactly like the mock."""
        store = PipelineStore(":memory:")
        runner = _runner(_FakeClaude())
        spine = PipelineSpine(store)
        executor = EffectExecutor(store, runner)
        res = drive(spine, executor, PipelineEvent(
            kind=EV_IDEA_RECEIVED, tenant="t", owner_user_id="u1",
            actor_user_id="u1", payload={"idea_text": "утренний картинг"}))
        rid = next(i.run_id for i in res.intents if i.run_id)
        # script generated via real runner → artifact stored
        kinds = {a.kind for a in store.get_artifacts(rid)}
        self.assertIn("script", kinds)
        run = store.get_run(rid)
        res2 = drive(spine, executor, PipelineEvent(
            kind=EV_APPROVE, run_id=rid, stage=run.stage,
            stage_version=run.stage_version, actor_user_id="u1"))
        self.assertEqual(store.get_run(rid).stage, STAGE_COVER)
        store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
