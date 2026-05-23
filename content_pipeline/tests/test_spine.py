"""Slice 1a invariants — stdlib unittest, no pytest, no network, no money.

Run:  python -m unittest content_pipeline.tests.test_spine -v
"""
from __future__ import annotations

import pathlib
import unittest

from content_pipeline.models import (
    PipelineEvent,
    EV_IDEA_RECEIVED, EV_APPROVE, EV_SKIP, EV_UPLOAD_VOICE, EV_CONFIRM_PAID, EV_RESUME,
    UI_SHOW_STEP, UI_REQUEST_INPUT, UI_SHOW_COST_GATE, UI_SHOW_RESUME_LIST,
    UI_SHOW_STALE_STATE, UI_SHOW_STATUS,
    EFF_START_PAID_JOB,
    STAGE_SCRIPT, STAGE_COVER, STAGE_VOICE, STAGE_AVATAR,
    GATE_PENDING, GATE_CONFIRMED, GATE_SPENT,
)
from content_pipeline.store import PipelineStore
from content_pipeline.steps import MockStepRunner
from content_pipeline.core import PipelineSpine, EffectExecutor, drive


def _find(intents, kind):
    return [i for i in intents if i.kind == kind]


def _action(intent, action_name):
    for a in intent.actions:
        if a.action == action_name:
            return a
    return None


class SpineTestBase(unittest.TestCase):
    def setUp(self):
        self.store = PipelineStore(":memory:")
        self.steps = MockStepRunner()
        self.spine = PipelineSpine(self.store)
        self.executor = EffectExecutor(self.store, self.steps)

    def tearDown(self):
        self.store.close()

    def _idea(self, owner="u1"):
        return drive(self.spine, self.executor, PipelineEvent(
            kind=EV_IDEA_RECEIVED, tenant="t", owner_user_id=owner,
            actor_user_id=owner, payload={"idea_text": "утренний картинг"}))

    def _run_id_of(self, res):
        for i in res.intents:
            if i.run_id:
                return i.run_id
        self.fail("no run_id in intents")

    def _drive_to_voice(self, owner="u1"):
        """idea → (script ready) → approve → (cover ready) → approve → voice."""
        res = self._idea(owner)
        rid = self._run_id_of(res)
        run = self.store.get_run(rid)
        # approve script
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_APPROVE, run_id=rid, stage=run.stage,
            stage_version=run.stage_version, actor_user_id=owner))
        run = self.store.get_run(rid)
        # approve cover
        res2 = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_APPROVE, run_id=rid, stage=run.stage,
            stage_version=run.stage_version, actor_user_id=owner))
        return rid, res2

    def _drive_to_gate(self, owner="u1"):
        rid, _ = self._drive_to_voice(owner)
        run = self.store.get_run(rid)
        self.assertEqual(run.stage, STAGE_VOICE)
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_UPLOAD_VOICE, run_id=rid, stage=STAGE_VOICE,
            stage_version=run.stage_version, actor_user_id=owner,
            payload={"audio_path": "/tmp/v.mp3"}))
        return rid, res


class TestTransitions(SpineTestBase):
    def test_create_run_from_idea(self):
        res = self._idea()
        rid = self._run_id_of(res)
        run = self.store.get_run(rid)
        self.assertIsNotNone(run)
        self.assertEqual(run.stage, STAGE_SCRIPT)
        # script step ran (mock) → an approve step is shown
        self.assertTrue(_find(res.intents, UI_SHOW_STEP))
        arts = {a.kind for a in self.store.get_artifacts(rid)}
        self.assertIn("script", arts)

    def test_approve_script_advances_to_cover(self):
        res = self._idea()
        rid = self._run_id_of(res)
        run = self.store.get_run(rid)
        res2 = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_APPROVE, run_id=rid, stage=run.stage,
            stage_version=run.stage_version, actor_user_id="u1"))
        self.assertEqual(self.store.get_run(rid).stage, STAGE_COVER)
        self.assertTrue(_find(res2.intents, UI_SHOW_STEP))

    def test_approve_cover_advances_to_voice(self):
        rid, res = self._drive_to_voice()
        self.assertEqual(self.store.get_run(rid).stage, STAGE_VOICE)
        self.assertTrue(_find(res.intents, UI_REQUEST_INPUT))

    def test_voice_uploaded_advances_to_avatar_gate(self):
        rid, res = self._drive_to_gate()
        run = self.store.get_run(rid)
        self.assertEqual(run.stage, STAGE_AVATAR)
        self.assertEqual(run.paid_gate, GATE_PENDING)
        self.assertTrue(_find(res.intents, UI_SHOW_COST_GATE))

    def test_skip_records_event(self):
        res = self._idea()
        rid = self._run_id_of(res)
        run = self.store.get_run(rid)
        drive(self.spine, self.executor, PipelineEvent(
            kind=EV_SKIP, run_id=rid, stage=run.stage,
            stage_version=run.stage_version, actor_user_id="u1"))
        types = [e["event_type"] for e in self.store.get_events(rid)]
        self.assertIn("stage_skipped", types)


class TestCostGate(SpineTestBase):
    def test_avatar_gate_does_not_start_paid_job(self):
        rid, res = self._drive_to_gate()
        paid = [e for e in res.effects if e.kind == EFF_START_PAID_JOB]
        self.assertEqual(paid, [], "reaching the gate must NOT start a paid job")
        self.assertNotIn(("start_paid_job", rid, STAGE_AVATAR),
                         [c for c in self.steps.calls])

    def test_confirm_paid_records_paid_confirmed_once(self):
        rid, _ = self._drive_to_gate()
        run = self.store.get_run(rid)
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_CONFIRM_PAID, run_id=rid, stage=STAGE_AVATAR,
            stage_version=run.stage_version, actor_user_id="u1"))
        paid = [e for e in res.effects if e.kind == EFF_START_PAID_JOB]
        self.assertEqual(len(paid), 1)
        self.assertEqual(self.store.get_run(rid).paid_gate, GATE_SPENT)
        types = [e["event_type"] for e in self.store.get_events(rid)]
        self.assertIn("paid_confirmed", types)
        self.assertEqual(types.count("paid_confirmed"), 1)

    def test_double_confirm_paid_is_idempotent(self):
        rid, _ = self._drive_to_gate()
        run = self.store.get_run(rid)
        gate_version = run.stage_version  # the version the gate button carries
        # First confirm — succeeds, starts exactly one job.
        res1 = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_CONFIRM_PAID, run_id=rid, stage=STAGE_AVATAR,
            stage_version=gate_version, actor_user_id="u1"))
        # Second confirm — same (now stale) version, must NOT start a second job.
        res2 = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_CONFIRM_PAID, run_id=rid, stage=STAGE_AVATAR,
            stage_version=gate_version, actor_user_id="u1"))
        paid1 = [e for e in res1.effects if e.kind == EFF_START_PAID_JOB]
        paid2 = [e for e in res2.effects if e.kind == EFF_START_PAID_JOB]
        self.assertEqual(len(paid1), 1)
        self.assertEqual(len(paid2), 0, "double-click must not start a second job")
        starts = [c for c in self.steps.calls if c[0] == "start_paid_job"]
        self.assertEqual(len(starts), 1)


class TestStaleAndResume(SpineTestBase):
    def test_stale_stage_version_rejected(self):
        res = self._idea()
        rid = self._run_id_of(res)
        run = self.store.get_run(rid)
        stale_version = run.stage_version - 1  # an old button
        res2 = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_APPROVE, run_id=rid, stage=run.stage,
            stage_version=stale_version, actor_user_id="u1"))
        self.assertTrue(_find(res2.intents, UI_SHOW_STALE_STATE))
        # stage unchanged
        self.assertEqual(self.store.get_run(rid).stage, STAGE_SCRIPT)
        types = [e["event_type"] for e in self.store.get_events(rid)]
        self.assertIn("stale_action_rejected", types)

    def test_start_resume_lists_active_runs(self):
        self._idea(owner="u1")
        self._idea(owner="u1")
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_RESUME, owner_user_id="u1"))
        lists = _find(res.intents, UI_SHOW_RESUME_LIST)
        self.assertTrue(lists)
        self.assertEqual(len(lists[0].data["runs"]), 2)

    def test_resume_empty_when_no_runs(self):
        res = drive(self.spine, self.executor, PipelineEvent(
            kind=EV_RESUME, owner_user_id="nobody"))
        self.assertTrue(_find(res.intents, UI_SHOW_STATUS))


class TestNotionMirror(SpineTestBase):
    def test_notion_failure_sets_sync_pending(self):
        res = self._idea()
        rid = self._run_id_of(res)
        self.store.record_notion_sync(rid, ok=False)
        self.assertEqual(self.store.get_run(rid).notion_sync_pending, 1)
        self.store.record_notion_sync(rid, ok=True, notion_status="Сценарий")
        self.assertEqual(self.store.get_run(rid).notion_sync_pending, 0)


class TestBoundary(unittest.TestCase):
    def test_core_has_no_bot_imports(self):
        """The core package must not depend on the bot layer (portability)."""
        pkg_dir = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for py in pkg_dir.glob("*.py"):  # top-level modules only, not tests/
            src = py.read_text(encoding="utf-8")
            for line in src.splitlines():
                s = line.strip()
                if s.startswith("import bot") or s.startswith("from bot"):
                    offenders.append(f"{py.name}: {s}")
        self.assertEqual(offenders, [], f"core must not import bot layer: {offenders}")

    def test_core_has_no_tenant_constants(self):
        """No hardcoded client name leaking into the tenant-agnostic core."""
        pkg_dir = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for py in pkg_dir.glob("*.py"):
            src = py.read_text(encoding="utf-8").lower()
            if "maksim" in src:
                offenders.append(py.name)
        self.assertEqual(offenders, [], f"core must be tenant-agnostic: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
