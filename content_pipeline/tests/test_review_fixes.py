"""Tests for the ChatGPT-review RED+YELLOW fixes (C1/C3/C4/M1/M5).

Run: python -m unittest content_pipeline.tests.test_review_fixes -v
"""
from __future__ import annotations

import unittest

from content_pipeline.store import PipelineStore
from content_pipeline.steps import MockStepRunner, PreSubmitError
from content_pipeline.core import PipelineSpine, EffectExecutor, drive
from content_pipeline.models import (
    PipelineEvent,
    EV_IDEA_RECEIVED, EV_APPROVE, EV_SKIP, EV_UPLOAD_VOICE, EV_CONFIRM_PAID, EV_JOB_COMPLETED,
    UI_SHOW_COST_GATE, EFF_START_PAID_JOB,
    STAGE_VOICE, STAGE_AVATAR, STAGE_DONE,
    ST_RUNNING_JOB, ST_COMPLETED, ST_CANCELLED, ST_WAITING_CONFIRM,
    GATE_PENDING, GATE_CONFIRMED, GATE_SPENT,
)


class Base(unittest.TestCase):
    def setUp(self):
        self.store = PipelineStore(":memory:")
        self.steps = MockStepRunner()
        self.spine = PipelineSpine(self.store)
        self.executor = EffectExecutor(self.store, self.steps)

    def tearDown(self):
        self.store.close()

    def _new(self, owner="u1"):
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_IDEA_RECEIVED, tenant="t", owner_user_id=owner, actor_user_id=owner,
            chat_id="c1", payload={"idea_text": "картинг"}))
        return next(i.run_id for i in res.intents if i.run_id)

    def _to_voice(self, owner="u1"):
        rid = self._new(owner)
        r = self.store.get_run(rid)
        drive(self.spine, self.executor, PipelineEvent(kind=EV_APPROVE, tenant="t",
              owner_user_id=owner, run_id=rid, stage=r.stage, stage_version=r.stage_version))
        r = self.store.get_run(rid)
        drive(self.spine, self.executor, PipelineEvent(kind=EV_APPROVE, tenant="t",
              owner_user_id=owner, run_id=rid, stage=r.stage, stage_version=r.stage_version))
        return rid

    def _to_gate(self, owner="u1"):
        rid = self._to_voice(owner)
        r = self.store.get_run(rid)
        drive(self.spine, self.executor, PipelineEvent(kind=EV_UPLOAD_VOICE, tenant="t",
              owner_user_id=owner, run_id=rid, stage=STAGE_VOICE, stage_version=r.stage_version,
              payload={"audio_path": "/v.mp3"}))
        return rid


class TestOwnerCheck(Base):  # C4
    def test_foreign_actor_cannot_confirm_paid(self):
        rid = self._to_gate(owner="owner1")
        r = self.store.get_run(rid)
        # a DIFFERENT user clicks the same gate button
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_CONFIRM_PAID, tenant="t", owner_user_id="intruder",
            run_id=rid, stage=STAGE_AVATAR, stage_version=r.stage_version))
        paid = [e for e in res.effects if e.kind == EFF_START_PAID_JOB]
        self.assertEqual(paid, [], "foreign actor must not start a paid job")
        self.assertEqual(self.store.get_run(rid).paid_gate, GATE_PENDING)

    def test_foreign_actor_cannot_approve(self):
        rid = self._new(owner="owner1")
        r = self.store.get_run(rid)
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_APPROVE, tenant="t", owner_user_id="intruder",
            run_id=rid, stage=r.stage, stage_version=r.stage_version))
        self.assertEqual(self.store.get_run(rid).stage, r.stage)  # unchanged


class TestSkipVoice(Base):  # M1
    def test_skip_voice_cancels_run_not_paid_gate(self):
        rid = self._to_voice()
        r = self.store.get_run(rid)
        self.assertEqual(r.stage, STAGE_VOICE)
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_SKIP, tenant="t", owner_user_id="u1",
            run_id=rid, stage=STAGE_VOICE, stage_version=r.stage_version))
        r = self.store.get_run(rid)
        self.assertEqual(r.status, ST_CANCELLED)
        self.assertEqual(r.active, 0)
        self.assertNotEqual(r.stage, STAGE_AVATAR)  # never reached the paid gate


class TestPreSubmitRetryable(Base):  # C3
    class _PreFailRunner(MockStepRunner):
        def start_paid_job(self, run_id, kind, config):
            raise PreSubmitError("transient network before submit")

    def test_pre_submit_failure_rolls_back_to_gate(self):
        self.executor = EffectExecutor(self.store, self._PreFailRunner())
        rid = self._to_gate()
        r = self.store.get_run(rid)
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_CONFIRM_PAID, tenant="t", owner_user_id="u1",
            run_id=rid, stage=STAGE_AVATAR, stage_version=r.stage_version))
        r = self.store.get_run(rid)
        # retryable: back at the gate, pending, active, not failed/wedged
        self.assertEqual(r.status, ST_WAITING_CONFIRM)
        self.assertEqual(r.paid_gate, GATE_PENDING)
        self.assertEqual(r.active, 1)
        self.assertIsNone(r.current_job_id)
        self.assertTrue([i for i in res.intents if i.kind == UI_SHOW_COST_GATE])


class TestRecovery(Base):  # C1
    def test_recover_wedged_paid_run(self):
        rid = self._to_gate()
        r = self.store.get_run(rid)
        # simulate a crash right after confirm CAS, before provider submit
        self.store.cas_transition(rid, expect_stage=STAGE_AVATAR,
                                  expect_version=r.stage_version, new_stage=STAGE_AVATAR,
                                  new_status=ST_RUNNING_JOB, set_paid_gate=GATE_CONFIRMED,
                                  expect_paid_gate=GATE_PENDING)
        wedged = self.store.get_run(rid)
        self.assertEqual(wedged.status, ST_RUNNING_JOB)
        self.assertIsNone(wedged.current_job_id)
        n = self.store.recover_wedged_paid_runs()
        self.assertEqual(n, 1)
        rec = self.store.get_run(rid)
        self.assertEqual(rec.status, ST_WAITING_CONFIRM)
        self.assertEqual(rec.paid_gate, GATE_PENDING)


class TestTerminalActive(Base):  # M5
    def test_completed_run_is_inactive(self):
        rid = self._to_gate()
        r = self.store.get_run(rid)
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_CONFIRM_PAID, tenant="t", owner_user_id="u1",
            run_id=rid, stage=STAGE_AVATAR, stage_version=r.stage_version))
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_JOB_COMPLETED, run_id=rid, payload={"url": "http://x/a.mp4"}))
        r = self.store.get_run(rid)
        self.assertEqual(r.stage, STAGE_DONE)
        self.assertEqual(r.active, 0)
        self.assertNotIn(rid, [x.run_id for x in self.store.get_active_runs("u1")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
