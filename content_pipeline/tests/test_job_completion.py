"""Slice 1c.1 — async provider-job outcome handling (fake provider, no money).

Simulates the poller feeding job_completed / job_failed back into the spine
after the user confirmed the paid avatar step.

Run: python -m unittest content_pipeline.tests.test_job_completion -v
"""
from __future__ import annotations

import unittest

from content_pipeline.store import PipelineStore
from content_pipeline.steps import MockStepRunner
from content_pipeline.core import PipelineSpine, EffectExecutor, drive
from content_pipeline.models import (
    PipelineEvent,
    EV_IDEA_RECEIVED, EV_APPROVE, EV_UPLOAD_VOICE, EV_CONFIRM_PAID,
    EV_JOB_COMPLETED, EV_JOB_FAILED,
    UI_SHOW_RESULT, UI_SHOW_ERROR,
    STAGE_AVATAR, STAGE_DONE, STAGE_VOICE,
    ST_RUNNING_JOB, ST_COMPLETED, ST_FAILED,
    GATE_SPENT,
)


class JobBase(unittest.TestCase):
    def setUp(self):
        self.store = PipelineStore(":memory:")
        self.steps = MockStepRunner()
        self.spine = PipelineSpine(self.store)
        self.executor = EffectExecutor(self.store, self.steps)

    def tearDown(self):
        self.store.close()

    def _to_confirmed(self, owner="u1"):
        """Drive idea→...→avatar gate→confirm_paid (mock job started)."""
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_IDEA_RECEIVED, tenant="t", owner_user_id=owner,
            actor_user_id=owner, chat_id="555",
            payload={"idea_text": "картинг"}))
        rid = next(i.run_id for i in res.intents if i.run_id)
        run = self.store.get_run(rid)
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_APPROVE, run_id=rid, stage=run.stage,
            stage_version=run.stage_version, actor_user_id=owner))
        run = self.store.get_run(rid)
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_APPROVE, run_id=rid, stage=run.stage,
            stage_version=run.stage_version, actor_user_id=owner))
        run = self.store.get_run(rid)  # voice
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_UPLOAD_VOICE, run_id=rid, stage=STAGE_VOICE,
            stage_version=run.stage_version, actor_user_id=owner,
            payload={"audio_path": "/tmp/v.mp3"}))
        run = self.store.get_run(rid)  # avatar gate
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_CONFIRM_PAID, run_id=rid, stage=STAGE_AVATAR,
            stage_version=run.stage_version, actor_user_id=owner))
        return rid


class TestJobCompletion(JobBase):
    def test_run_persists_chat_id(self):
        rid = self._to_confirmed()
        self.assertEqual(self.store.get_run(rid).chat_id, "555")

    def test_confirmed_run_is_awaiting_job(self):
        rid = self._to_confirmed()
        run = self.store.get_run(rid)
        self.assertEqual(run.stage, STAGE_AVATAR)
        self.assertEqual(run.status, ST_RUNNING_JOB)
        self.assertEqual(run.paid_gate, GATE_SPENT)
        self.assertIsNotNone(run.current_job_id)
        awaiting = self.store.get_runs_awaiting_job()
        self.assertIn(rid, [r.run_id for r in awaiting])

    def test_job_completed_delivers_and_finishes(self):
        rid = self._to_confirmed()
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_JOB_COMPLETED, run_id=rid,
            payload={"path": "/proj/avatar.mp4", "url": "http://x/a.mp4", "duration": 42}))
        run = self.store.get_run(rid)
        self.assertEqual(run.stage, STAGE_DONE)
        self.assertEqual(run.status, ST_COMPLETED)
        self.assertTrue([i for i in res.intents if i.kind == UI_SHOW_RESULT])
        kinds = {a.kind for a in self.store.get_artifacts(rid)}
        self.assertIn("avatar_video", kinds)
        # no longer polled
        self.assertNotIn(rid, [r.run_id for r in self.store.get_runs_awaiting_job()])

    def test_duplicate_completion_is_ignored(self):
        rid = self._to_confirmed()
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_JOB_COMPLETED, run_id=rid, payload={"path": "/a.mp4"}))
        res2 = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_JOB_COMPLETED, run_id=rid, payload={"path": "/a.mp4"}))
        # second one is a no-op (no extra result intent, no second artifact)
        self.assertEqual([i for i in res2.intents if i.kind == UI_SHOW_RESULT], [])
        vids = [a for a in self.store.get_artifacts(rid) if a.kind == "avatar_video"]
        self.assertEqual(len(vids), 1)

    def test_job_failed_marks_failed(self):
        rid = self._to_confirmed()
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_JOB_FAILED, run_id=rid, payload={"error": "provider boom"}))
        self.assertEqual(self.store.get_run(rid).status, ST_FAILED)
        self.assertTrue([i for i in res.intents if i.kind == UI_SHOW_ERROR])


class TestPaidSubmitFailure(JobBase):
    """C1: if start_paid_job raises AFTER the confirm CAS, the run must fail
    cleanly (not wedge forever in running_job with no job id)."""

    class _FailingRunner(MockStepRunner):
        def start_paid_job(self, run_id, kind, config):
            raise RuntimeError("heygen upload exploded")

    def setUp(self):
        super().setUp()
        self.steps = self._FailingRunner()
        self.executor = EffectExecutor(self.store, self.steps)

    def test_submit_failure_fails_run_not_wedged(self):
        rid = self._to_confirmed()  # confirm_paid → start_paid_job raises
        run = self.store.get_run(rid)
        self.assertEqual(run.status, ST_FAILED, "run must be failed, not stuck running_job")
        self.assertIsNone(run.current_job_id)
        # not picked up by the poller (would loop forever otherwise)
        self.assertNotIn(rid, [r.run_id for r in self.store.get_runs_awaiting_job()])
        types = [e["event_type"] for e in self.store.get_events(rid)]
        self.assertIn("job_start_failed", types)
        self.assertIn("job_failed", types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
