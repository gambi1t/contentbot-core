"""PipelineSpine — the state machine, plus the EffectExecutor and a drive loop.

Boundary rules (enforced by tests):
  * this module imports NOTHING from the bot layer;
  * no tenant constants (no client names, provider ids, Notion status strings,
    Telegram calls);
  * ``handle()`` decides transitions + returns (UIIntent, EffectCommand) — it
    never executes side effects itself;
  * paid effects are emitted ONLY after an explicit, version-checked
    ``confirm_paid`` (auto-advance can never spend money).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import plans
from .cost_policy import is_paid_stage
from .models import (
    Decision,
    EffectCommand,
    PipelineEvent,
    Run,
    UIAction,
    UIIntent,
    InputField,
    # stages
    STAGE_VOICE,
    STAGE_AVATAR,
    STAGE_DONE,
    # statuses
    ST_RUNNING_JOB,
    ST_WAITING_USER,
    ST_WAITING_INPUT,
    ST_WAITING_CONFIRM,
    ST_COMPLETED,
    ST_FAILED,
    # gates
    GATE_NONE,
    GATE_PENDING,
    GATE_CONFIRMED,
    GATE_SPENT,
    # event kinds
    EV_IDEA_RECEIVED,
    EV_APPROVE,
    EV_SKIP,
    EV_UPLOAD_VOICE,
    EV_CONFIRM_PAID,
    EV_OPEN_MATERIALS,
    EV_RESUME,
    EV_STEP_COMPLETED,
    EV_JOB_COMPLETED,
    EV_JOB_FAILED,
    # UI kinds
    UI_SHOW_STEP,
    UI_SHOW_RESUME_LIST,
    UI_REQUEST_INPUT,
    UI_SHOW_COST_GATE,
    UI_SHOW_STATUS,
    UI_SHOW_MATERIALS,
    UI_SHOW_STALE_STATE,
    UI_SHOW_ERROR,
    UI_SHOW_RESULT,
    # effect kinds
    EFF_GENERATE_SCRIPT,
    EFF_GENERATE_COVER,
    EFF_START_PAID_JOB,
)
from .store import PipelineStore


class PipelineSpine:
    def __init__(self, store: PipelineStore, default_plan: str = plans.PLAN_AVATAR) -> None:
        self.store = store
        self.default_plan = default_plan

    # ── entry point ───────────────────────────────────────────────────────────
    def handle(self, ev: PipelineEvent) -> Decision:
        if ev.kind == EV_IDEA_RECEIVED:
            return self._on_idea(ev)
        if ev.kind == EV_RESUME:
            return self._on_resume(ev)
        if ev.kind == EV_STEP_COMPLETED:
            return self._on_step_completed(ev)
        if ev.kind == EV_APPROVE:
            return self._on_approve(ev)
        if ev.kind == EV_SKIP:
            return self._on_skip(ev)
        if ev.kind == EV_UPLOAD_VOICE:
            return self._on_upload_voice(ev)
        if ev.kind == EV_CONFIRM_PAID:
            return self._on_confirm_paid(ev)
        if ev.kind == EV_JOB_COMPLETED:
            return self._on_job_completed(ev)
        if ev.kind == EV_JOB_FAILED:
            return self._on_job_failed(ev)
        if ev.kind == EV_OPEN_MATERIALS:
            return self._on_open_materials(ev)
        return Decision(intents=[UIIntent(kind=UI_SHOW_ERROR, body=f"unknown event: {ev.kind}")])

    # ── handlers ──────────────────────────────────────────────────────────────
    def _on_idea(self, ev: PipelineEvent) -> Decision:
        plan = ev.payload.get("plan") or self.default_plan
        stage = plans.first_stage(plan)
        run = self.store.create_run(
            tenant=ev.tenant,
            owner_user_id=ev.owner_user_id,
            actor_user_id=ev.actor_user_id or ev.owner_user_id,
            chat_id=ev.chat_id,
            plan=plan,
            stage=stage,
            status=ST_RUNNING_JOB,
            notion_page_id=ev.notion_page_id,
        )
        self.store.add_event(run.run_id, "run_created", to_stage=stage,
                             actor_user_id=ev.actor_user_id,
                             payload={"idea": ev.payload.get("idea_text", "")})
        eff = EffectCommand(
            kind=EFF_GENERATE_SCRIPT, run_id=run.run_id,
            payload={"idea_text": ev.payload.get("idea_text", "")},
            idempotency_key=f"{run.run_id}:script",
        )
        intent = UIIntent(kind=UI_SHOW_STATUS, run_id=run.run_id,
                          body="Пишу сценарий…", data={"stage": stage})
        return Decision(intents=[intent], effects=[eff])

    def _on_step_completed(self, ev: PipelineEvent) -> Decision:
        """A cheap step finished (script/cover). Move run to waiting_user/input
        WITHOUT bumping the version, then show the step for approval/input."""
        run = self._require_run(ev.run_id)
        if run is None:
            return _err("run not found")
        stage = ev.stage or run.stage
        if stage == STAGE_VOICE:
            self.store.set_status(run.run_id, ST_WAITING_INPUT)
        else:
            self.store.set_status(run.run_id, ST_WAITING_USER)
        run = self.store.get_run(run.run_id)
        return Decision(intents=[self._step_intent(run)])

    def _on_approve(self, ev: PipelineEvent) -> Decision:
        run = self._require_run(ev.run_id)
        if run is None:
            return _err("run not found")
        nxt = plans.next_stage(run.plan, run.stage)
        new_status = self._status_for_stage(nxt)
        ok = self.store.cas_transition(
            run.run_id, expect_stage=run.stage, expect_version=ev.stage_version or -1,
            new_stage=nxt, new_status=new_status,
            set_paid_gate=(GATE_PENDING if is_paid_stage(nxt) else None),
        )
        if not ok:
            return self._stale(run.run_id, ev)
        self.store.add_event(run.run_id, "user_approved", from_stage=run.stage,
                             to_stage=nxt, actor_user_id=ev.actor_user_id)
        self.store.add_event(run.run_id, "stage_advanced", from_stage=run.stage,
                             to_stage=nxt)
        return self._enter_stage(self.store.get_run(run.run_id))

    def _on_skip(self, ev: PipelineEvent) -> Decision:
        run = self._require_run(ev.run_id)
        if run is None:
            return _err("run not found")
        nxt = plans.next_stage(run.plan, run.stage)
        new_status = self._status_for_stage(nxt)
        ok = self.store.cas_transition(
            run.run_id, expect_stage=run.stage, expect_version=ev.stage_version or -1,
            new_stage=nxt, new_status=new_status,
            set_paid_gate=(GATE_PENDING if is_paid_stage(nxt) else None),
        )
        if not ok:
            return self._stale(run.run_id, ev)
        # Skip is an explicit decision — record it so we can later tell
        # "skipped" from "failed" from "not yet done".
        self.store.add_event(run.run_id, "stage_skipped", from_stage=run.stage,
                             to_stage=nxt, actor_user_id=ev.actor_user_id,
                             payload={"reason": "user_clicked_skip"})
        return self._enter_stage(self.store.get_run(run.run_id))

    def _on_upload_voice(self, ev: PipelineEvent) -> Decision:
        run = self._require_run(ev.run_id)
        if run is None:
            return _err("run not found")
        if run.stage != STAGE_VOICE:
            return self._stale(run.run_id, ev)
        nxt = plans.next_stage(run.plan, run.stage)  # → avatar
        ok = self.store.cas_transition(
            run.run_id, expect_stage=STAGE_VOICE, expect_version=ev.stage_version or -1,
            new_stage=nxt, new_status=self._status_for_stage(nxt),
            set_paid_gate=(GATE_PENDING if is_paid_stage(nxt) else None),
        )
        if not ok:
            return self._stale(run.run_id, ev)
        # Own-voice upload is FREE — store the artifact, no provider call here.
        self.store.add_artifact(run.run_id, "voice",
                                path=ev.payload.get("audio_path"),
                                meta={"source": "own_voice"})
        self.store.add_event(run.run_id, "user_approved", from_stage=STAGE_VOICE,
                             to_stage=nxt, actor_user_id=ev.actor_user_id,
                             payload={"voice": "own"})
        self.store.add_event(run.run_id, "stage_advanced", from_stage=STAGE_VOICE,
                             to_stage=nxt)
        return self._enter_stage(self.store.get_run(run.run_id))

    def _on_confirm_paid(self, ev: PipelineEvent) -> Decision:
        run = self._require_run(ev.run_id)
        if run is None:
            return _err("run not found")
        # Single atomic guard does double duty:
        #   * version mismatch (stale / double-click)  → no change
        #   * paid_gate already not 'pending' (already confirmed/spent) → no change
        ok = self.store.cas_transition(
            run.run_id, expect_stage=STAGE_AVATAR, expect_version=ev.stage_version or -1,
            new_stage=STAGE_AVATAR, new_status=ST_RUNNING_JOB,
            set_paid_gate=GATE_CONFIRMED, expect_paid_gate=GATE_PENDING,
        )
        if not ok:
            cur = self.store.get_run(run.run_id)
            if cur and cur.paid_gate in (GATE_CONFIRMED, GATE_SPENT):
                # Already running — never emit a second paid effect.
                return Decision(intents=[UIIntent(
                    kind=UI_SHOW_STATUS, run_id=run.run_id,
                    body="Аватар уже запущен — жди результат.")])
            return self._stale(run.run_id, ev)
        self.store.add_event(run.run_id, "paid_confirmed", from_stage=STAGE_AVATAR,
                             actor_user_id=ev.actor_user_id)
        eff = EffectCommand(
            kind=EFF_START_PAID_JOB, run_id=run.run_id,
            payload={"stage": STAGE_AVATAR},
            idempotency_key=f"{run.run_id}:avatar",
        )
        intent = UIIntent(kind=UI_SHOW_STATUS, run_id=run.run_id,
                          body="Запускаю генерацию аватара…")
        return Decision(intents=[intent], effects=[eff])

    def _on_job_completed(self, ev: PipelineEvent) -> Decision:
        """Provider render finished (fed by the poller). Store the avatar video,
        advance avatar→done, deliver the result. Idempotent: a duplicate
        completion (poller raced) is ignored once the run is already done."""
        run = self._require_run(ev.run_id)
        if run is None:
            return _err("run not found")
        if run.stage != STAGE_AVATAR or run.status != ST_RUNNING_JOB:
            # Already delivered / not in a job → ignore duplicate poller hit.
            return Decision()
        self.store.add_artifact(
            run.run_id, "avatar_video",
            path=ev.payload.get("path"), url=ev.payload.get("url"),
            meta={"duration": ev.payload.get("duration"),
                  "job_id": run.current_job_id},
        )
        ok = self.store.cas_transition(
            run.run_id, expect_stage=STAGE_AVATAR, expect_version=run.stage_version,
            new_stage=STAGE_DONE, new_status=ST_COMPLETED,
        )
        if not ok:
            return Decision()  # raced with another transition — ignore
        self.store.add_event(run.run_id, "job_completed", from_stage=STAGE_AVATAR,
                             to_stage=STAGE_DONE,
                             payload={"job_id": run.current_job_id})
        return Decision(intents=[UIIntent(
            kind=UI_SHOW_RESULT, run_id=run.run_id,
            title="Аватар готов ✅",
            body="Видео аватара сгенерировано.",
            data={"path": ev.payload.get("path"), "url": ev.payload.get("url"),
                  "duration": ev.payload.get("duration")},
        )])

    def _on_job_failed(self, ev: PipelineEvent) -> Decision:
        run = self._require_run(ev.run_id)
        if run is None:
            return _err("run not found")
        if run.stage != STAGE_AVATAR or run.status != ST_RUNNING_JOB:
            return Decision()
        self.store.set_status(run.run_id, ST_FAILED)
        self.store.add_event(run.run_id, "job_failed", from_stage=STAGE_AVATAR,
                             payload={"job_id": run.current_job_id,
                                      "error": ev.payload.get("error")})
        return Decision(intents=[UIIntent(
            kind=UI_SHOW_ERROR, run_id=run.run_id,
            title="Генерация аватара не удалась",
            body=f"Ошибка провайдера: {ev.payload.get('error') or 'неизвестно'}. "
                 "Открой карточку и попробуй ещё раз.",
        )])

    def _on_open_materials(self, ev: PipelineEvent) -> Decision:
        run = self._require_run(ev.run_id)
        if run is None:
            return _err("run not found")
        arts = self.store.get_artifacts(run.run_id)
        ready = [a.kind for a in arts]
        return Decision(intents=[UIIntent(
            kind=UI_SHOW_MATERIALS, run_id=run.run_id,
            title="Материалы",
            data={"ready": ready},
            body=("Готово: " + ", ".join(ready)) if ready else "Материалы пока не готовы.",
        )])

    def _on_resume(self, ev: PipelineEvent) -> Decision:
        runs = self.store.get_active_runs(ev.owner_user_id)
        if not runs:
            return Decision(intents=[UIIntent(
                kind=UI_SHOW_STATUS, body="Нет незавершённых роликов. Пришли идею.")])
        actions = [
            UIAction(label=f"Продолжить: {r.stage}", action="open_run",
                     run_id=r.run_id, stage=r.stage, stage_version=r.stage_version)
            for r in runs
        ]
        return Decision(intents=[UIIntent(
            kind=UI_SHOW_RESUME_LIST,
            title="Незавершённые ролики",
            data={"runs": [{"run_id": r.run_id, "stage": r.stage,
                            "stage_version": r.stage_version} for r in runs]},
            actions=actions,
        )])

    # ── stage entry / intent building ─────────────────────────────────────────
    def _enter_stage(self, run: Run) -> Decision:
        if run.stage == STAGE_DONE:
            self.store.set_status(run.run_id, ST_COMPLETED)
            return Decision(intents=[UIIntent(
                kind=UI_SHOW_STATUS, run_id=run.run_id, body="Готово ✅")])
        if is_paid_stage(run.stage):
            # Cost-gate: stop, show the price/warning, DO NOT start the job.
            self.store.add_event(run.run_id, "paid_gate_shown", to_stage=run.stage)
            return Decision(intents=[self._cost_gate_intent(run)])
        eff = self._effect_for_stage(run)
        if eff is None:
            # Stage needs user input/approval immediately (no generation step,
            # e.g. voice) → show that step right away.
            return Decision(intents=[self._step_intent(run)])
        # Cheap generated step → kick off its effect, show a working status; the
        # follow-up step_completed event will render the approval step.
        intent = UIIntent(kind=UI_SHOW_STATUS, run_id=run.run_id,
                          body=self._working_text(run.stage), data={"stage": run.stage})
        return Decision(intents=[intent], effects=[eff])

    def _effect_for_stage(self, run: Run):
        from .models import STAGE_SCRIPT, STAGE_COVER
        if run.stage == STAGE_SCRIPT:
            return EffectCommand(kind=EFF_GENERATE_SCRIPT, run_id=run.run_id,
                                 idempotency_key=f"{run.run_id}:script")
        if run.stage == STAGE_COVER:
            return EffectCommand(kind=EFF_GENERATE_COVER, run_id=run.run_id,
                                 idempotency_key=f"{run.run_id}:cover")
        # voice stage needs user input, no generation effect in 1a
        return None

    def _step_intent(self, run: Run) -> UIIntent:
        if run.stage == STAGE_VOICE:
            return UIIntent(
                kind=UI_REQUEST_INPUT, run_id=run.run_id,
                title="Озвучка",
                body="🎤 Пришли голосовое сообщение для озвучки аватара "
                     "(или пропусти этот шаг).",
                fields=[InputField(name="voice", kind="voice",
                                   prompt="голосовое для аватара")],
                actions=[
                    UIAction("⏭ Пропустить", "skip", run.run_id,
                             run.stage, run.stage_version),
                    UIAction("📥 Скачать материалы", "open_materials", run.run_id,
                             run.stage, run.stage_version, style="secondary"),
                ],
            )
        return UIIntent(
            kind=UI_SHOW_STEP, run_id=run.run_id,
            title=run.stage,
            body=f"Шаг «{run.stage}» готов. Утвердить?",
            actions=[
                UIAction("✅ Дальше", "approve", run.run_id,
                         run.stage, run.stage_version, style="primary"),
                UIAction("⏭ Пропустить", "skip", run.run_id,
                         run.stage, run.stage_version),
                UIAction("📥 Скачать материалы", "open_materials", run.run_id,
                         run.stage, run.stage_version, style="secondary"),
            ],
        )

    def _cost_gate_intent(self, run: Run) -> UIIntent:
        return UIIntent(
            kind=UI_SHOW_COST_GATE, run_id=run.run_id,
            title="Платная генерация",
            body=("Готов запустить генерацию аватара (HeyGen) — это платно.\n"
                  "Запустить?"),
            actions=[
                UIAction("💳 Запустить платно", "confirm_paid", run.run_id,
                         run.stage, run.stage_version, style="paid"),
                UIAction("⏭ Пропустить аватар", "skip", run.run_id,
                         run.stage, run.stage_version),
                UIAction("📥 Скачать материалы", "open_materials", run.run_id,
                         run.stage, run.stage_version, style="secondary"),
            ],
        )

    def _stale(self, run_id: str, ev: PipelineEvent) -> Decision:
        run = self.store.get_run(run_id)
        self.store.add_event(run_id, "stale_action_rejected",
                             actor_user_id=ev.actor_user_id,
                             payload={"action": ev.kind,
                                      "got_stage": ev.stage,
                                      "got_version": ev.stage_version,
                                      "cur_stage": run.stage if run else None,
                                      "cur_version": run.stage_version if run else None})
        body = "Эта кнопка устарела."
        actions = []
        if run:
            body = f"Эта кнопка устарела. Текущий этап: {run.stage}."
            actions = [UIAction("Открыть текущий шаг", "open_run", run.run_id,
                                run.stage, run.stage_version)]
        return Decision(intents=[UIIntent(
            kind=UI_SHOW_STALE_STATE, run_id=run_id, body=body, actions=actions)])

    # ── helpers ───────────────────────────────────────────────────────────────
    def _require_run(self, run_id):
        return self.store.get_run(run_id) if run_id else None

    @staticmethod
    def _status_for_stage(stage: str) -> str:
        if stage == STAGE_DONE:
            return ST_COMPLETED
        if stage == STAGE_VOICE:
            return ST_WAITING_INPUT
        if is_paid_stage(stage):
            return ST_WAITING_CONFIRM
        return ST_RUNNING_JOB

    @staticmethod
    def _working_text(stage: str) -> str:
        from .models import STAGE_SCRIPT, STAGE_COVER
        return {STAGE_SCRIPT: "Пишу сценарий…",
                STAGE_COVER: "Подбираю обложку…"}.get(stage, "Работаю…")


def _err(msg: str) -> Decision:
    return Decision(intents=[UIIntent(kind=UI_SHOW_ERROR, body=msg)])


# ════════════════════════════════════════════════════════════════════════════
#  Effect execution + drive loop
# ════════════════════════════════════════════════════════════════════════════

class EffectExecutor:
    """Executes EffectCommands via a (headless) StepRunner and the store.

    In Slice 1a the StepRunner is a mock — ``start_paid_provider_job`` does NOT
    call a real provider. The CAS guard in ``_on_confirm_paid`` already ensures
    this effect is emitted at most once per run.
    """

    def __init__(self, store: PipelineStore, step_runner, config: dict | None = None) -> None:
        self.store = store
        self.steps = step_runner
        self.config = config or {}

    def execute(self, eff: EffectCommand) -> list[PipelineEvent]:
        if eff.kind == EFF_GENERATE_SCRIPT:
            res = self.steps.generate_script(eff.run_id, eff.payload.get("idea_text", ""), self.config)
            self.store.add_artifact(eff.run_id, "script",
                                    text_content=res.get("text_content"),
                                    meta=res.get("meta"))
            return [PipelineEvent(kind=EV_STEP_COMPLETED, run_id=eff.run_id, stage="script")]
        if eff.kind == EFF_GENERATE_COVER:
            arts = {a.kind: a for a in self.store.get_artifacts(eff.run_id)}
            script_text = arts["script"].text_content if "script" in arts else ""
            res = self.steps.generate_cover_options(eff.run_id, script_text, self.config)
            self.store.add_artifact(eff.run_id, "cover", meta=res.get("meta"))
            return [PipelineEvent(kind=EV_STEP_COMPLETED, run_id=eff.run_id, stage="cover")]
        if eff.kind == EFF_START_PAID_JOB:
            # Enrich with the artifacts/params the provider needs (voice audio +
            # avatar look/version). Defaults: Avatar 3 (cheapest), brand look.
            arts = {a.kind: a for a in self.store.get_artifacts(eff.run_id)}
            voice = arts.get("voice")
            job_cfg = dict(self.config)
            job_cfg.update({
                "audio_path": voice.path if voice else None,
                "look_id": eff.payload.get("look_id"),
                "avatar_version": eff.payload.get("avatar_version", "v3"),
            })
            job_id = self.steps.start_paid_job(eff.run_id, eff.payload.get("stage", ""), job_cfg)
            self.store.set_current_job(eff.run_id, job_id, paid_gate=GATE_SPENT)
            self.store.add_event(eff.run_id, "job_started",
                                 payload={"job_id": job_id, "key": eff.idempotency_key})
            return []  # completion arrives async via the poller → EV_JOB_COMPLETED
        return []


@dataclass
class DriveResult:
    intents: list
    effects: list


def drive(spine: PipelineSpine, executor: EffectExecutor, event: PipelineEvent) -> DriveResult:
    """Feed one event, run any emitted effects, feed back their follow-up events,
    until the queue drains. Used by the Telegram adapter and the tests alike."""
    intents: list = []
    effects: list = []
    queue: list[PipelineEvent] = [event]
    guard = 0
    while queue:
        guard += 1
        if guard > 100:
            raise RuntimeError("drive loop did not converge (>100 steps)")
        ev = queue.pop(0)
        dec = spine.handle(ev)
        intents.extend(dec.intents)
        for eff in dec.effects:
            effects.append(eff)
            queue.extend(executor.execute(eff))
    return DriveResult(intents=intents, effects=effects)
