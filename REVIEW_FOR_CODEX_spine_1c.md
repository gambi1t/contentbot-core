# Внешнее ревью: спайн пайплайна (content_pipeline) 1a–1c

> **Что это.** Независимое ревью money-critical кода ПЕРЕД первым реальным
> HeyGen-рендером через прод. Внутренний субагент-ревьюер уже прошёл и нашёл 3
> бага (C1/C2/M1) — они **уже починены** (см. §3, не репорти их снова). Нужен
> свежий взгляд на то, что осталось. Спорь предметно, ищи money-leak'и и гонки.
>
> Дата: 2026-05-23. Репо: `gambi1t/contentbot-core`, ветка `maksim-prod`.
> Сравнение: `compare/main...maksim-prod`. 32 unit-теста зелёные.

## 1. Контекст
Спайн — отдельный **state-machine** конвейер «идея → ролик», вынесенный из
монолита `bot.py` (18.5k строк) **параллельным треком** (скрытая команда
`/spine`, рядом с живым ботом, его не трогает). Ядро `content_pipeline/`
**tenant-agnostic + transport-agnostic** (UI сейчас Telegram, потом, возможно,
Telegram Mini App — второй адаптер). Два выходных потока: `UIIntent` (что
показать) + `EffectCommand` (что сделать, в т.ч. платное, с idempotency).

**Деньги:** шаг avatar = реальный HeyGen-рендер ($). Поток: idea→script→cover→
voice(реальное голосовое)→**avatar cost-gate**→`confirm_paid`→`start_paid_job`
(upload+generate)→**фоновый poller** (каждые 20с, `heygen_check_status`)→
completed: доставка видео. Notion — зеркало (не источник). Состояние — SQLite
(`pipeline.db`), `run_id` ≠ `notion_page_id`.

## 2. Жёсткие инварианты (должны держаться)
1. Платный job нельзя запустить без явного `confirm_paid`.
2. Повторный `confirm_paid`/двойной клик не создаёт второй job.
3. Любая смена состояния — через CAS по `(stage, stage_version)`.
4. Ядро не импортирует `bot.py`, без клиент-констант.
5. Voice-фильтр не должен «съедать» обычные голосовые у живого `process_voice`.

## 3. Что внутренний ревью УЖЕ нашёл и ПОЧИНИЛ (НЕ репорти снова)
- **C1:** `start_paid_job` упал после confirm-CAS → run завис в `running_job` без
  `current_job_id`, poller игнорит (фильтрует NOT NULL), retry «уже запущено»
  навсегда. → executor ловит исключение → `EV_JOB_FAILED` (run failed).
- **C2:** синхронный `httpx` (status/upload/generate) блокировал общий
  event-loop. → `drive()` через `asyncio.to_thread` под `threading.Lock`; статус
  poller тоже `to_thread`; SQLite `check_same_thread=False` (сериализован lock'ом).
- **M1:** артефакт `avatar_video` писался до CAS → дубль на гонке. → после CAS.

## 4. На что прошу надавить (что МОГЛИ упустить)
1. **Money-leak после фиксов.** Остался ли путь, где деньги тратятся без
   `confirm_paid`, или дважды? Влияет ли C2 (`to_thread` + lock) на
   идемпотентность? Lock — `threading.Lock` (не async): корректно ли он
   сериализует и хендлеры, и poller?
2. **Poller-гонки.** `run_repeating` (APScheduler, по умолчанию max_instances=1)
   — достаточно ли этого + lock, или возможна двойная доставка? Что если рендер
   завис у HeyGen навсегда (poller крутит вечно)? Нужен ли таймаут/«стоп»?
3. **Рестарт во время рендера.** Run в `running_job` с `current_job_id` после
   рестарта подхватывается poller'ом — ок. Но `drive()` под lock из poller +
   одновременный пользовательский колбэк: deadlock/порядок?
4. **SQLite single-connection + lock.** Достаточно ли одного коннекта с
   `check_same_thread=False` + один глобальный lock на `drive()`, или есть
   доступ к store ВНЕ lock (например, voice-фильтр зовёт `get_active_runs`
   напрямую, не под lock — это race?).
5. **Voice-фильтр** (`_SpineAwaitingVoiceFilter`) зовёт `get_active_runs` на
   КАЖДОЕ голосовое (вне drive-lock). Race с конкурентным `drive()`, пишущим в ту
   же БД из другого потока? (sqlite чтение во время записи из другого потока).
6. **Что угодно ещё**, что ломает прод или теряет деньги/результат.

## 5. Формат ответа
Ranked Critical/Medium/Minor, каждый: файл + строка + однострочный фикс. Чисто —
скажи «чисто». Без стилевых придирок. Цель — добить money/concurrency перед первым
живым рендером.

---

## 6. Код (ветка maksim-prod)

Ниже — 4 ключевых файла логики. Простые (`models.py`, `plans.py`,
`cost_policy.py`, `steps.py`, `schema.sql`) опускаю — при нужде в репо.

### content_pipeline/core.py
```python
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
        # M1 fix: win the transition FIRST, then store the artifact. Otherwise a
        # racing duplicate completion could insert a second avatar_video before
        # the CAS rejects it.
        ok = self.store.cas_transition(
            run.run_id, expect_stage=STAGE_AVATAR, expect_version=run.stage_version,
            new_stage=STAGE_DONE, new_status=ST_COMPLETED,
        )
        if not ok:
            return Decision()  # raced with another transition — ignore
        self.store.add_artifact(
            run.run_id, "avatar_video",
            path=ev.payload.get("path"), url=ev.payload.get("url"),
            meta={"duration": ev.payload.get("duration"),
                  "job_id": run.current_job_id},
        )
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
            try:
                job_id = self.steps.start_paid_job(eff.run_id, eff.payload.get("stage", ""), job_cfg)
            except Exception as e:
                # C1 fix: submit failed AFTER the confirm CAS. No job exists, so
                # the poller (filters current_job_id NOT NULL) would never see
                # this run → it would wedge in running_job forever. Fail it
                # explicitly so the user gets feedback and isn't money-locked.
                self.store.add_event(eff.run_id, "job_start_failed",
                                     payload={"error": str(e)})
                return [PipelineEvent(kind=EV_JOB_FAILED, run_id=eff.run_id,
                                      payload={"error": f"submit: {e}"})]
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

```

### content_pipeline/store.py
```python
"""SQLite-backed store — the operational source of truth.

Why SQLite (not JSON, not Notion): transactions, optimistic concurrency, safe
across concurrent callbacks / Mini-App requests, survives restart, supports many
runs per card. Notion is only a mirror (see :meth:`record_notion_sync`).

The key primitive is :meth:`cas_transition` — a compare-and-swap on
``(stage, stage_version)``. It is BOTH the stage-advance mechanism and the
stale-button / concurrency guard: an action carrying an out-of-date
``stage_version`` simply doesn't match and changes nothing.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import (
    Run,
    Artifact,
    GATE_NONE,
    ST_RUNNING_JOB,
)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id() -> str:
    return uuid.uuid4().hex


class PipelineStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        # check_same_thread=False so the connection can be used from worker
        # threads (the adapter runs drive() via asyncio.to_thread to keep the
        # event loop free). All access is serialized by the adapter's drive lock,
        # so a single connection across threads stays safe.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL + busy_timeout matter for the real file db (concurrent callbacks /
        # future Mini App requests); harmless for :memory:.
        if db_path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Lightweight forward migrations for existing db files (no ORM)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(pipeline_runs)")}
        if "chat_id" not in cols:
            self.conn.execute("ALTER TABLE pipeline_runs ADD COLUMN chat_id TEXT")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── runs ────────────────────────────────────────────────────────────────
    def create_run(
        self,
        *,
        tenant: str,
        owner_user_id: str,
        plan: str,
        stage: str,
        status: str,
        actor_user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        notion_page_id: Optional[str] = None,
    ) -> Run:
        run_id = _new_run_id()
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO pipeline_runs
               (run_id, notion_page_id, tenant, owner_user_id, actor_user_id,
                chat_id, plan, stage, status, stage_version, active, paid_gate,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, notion_page_id, tenant, owner_user_id, actor_user_id,
             chat_id, plan, stage, status, 1, 1, GATE_NONE, now, now),
        )
        self.conn.commit()
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> Optional[Run]:
        row = self.conn.execute(
            "SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return _row_to_run(row) if row else None

    def get_active_runs(self, owner_user_id: str) -> list[Run]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_runs WHERE owner_user_id=? AND active=1 "
            "ORDER BY updated_at DESC",
            (owner_user_id,),
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_runs_awaiting_job(self) -> list[Run]:
        """Runs with a submitted provider job still rendering — for the poller."""
        rows = self.conn.execute(
            "SELECT * FROM pipeline_runs WHERE active=1 AND status=? "
            "AND current_job_id IS NOT NULL ORDER BY updated_at",
            (ST_RUNNING_JOB,),
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def cas_transition(
        self,
        run_id: str,
        *,
        expect_stage: str,
        expect_version: int,
        new_stage: str,
        new_status: str,
        set_paid_gate: Optional[str] = None,
        expect_paid_gate: Optional[str] = None,
        set_active: Optional[int] = None,
    ) -> bool:
        """Atomic compare-and-swap. Returns True iff exactly one row changed.

        Bumps ``stage_version`` on success — so a second click carrying the old
        version no longer matches (this is what makes paid-confirm idempotent
        and stale buttons inert).
        """
        sets = ["stage=?", "status=?", "stage_version=stage_version+1", "updated_at=?"]
        params: list = [new_stage, new_status, _utcnow()]
        if set_paid_gate is not None:
            sets.insert(2, "paid_gate=?")
            params.insert(2, set_paid_gate)
        if set_active is not None:
            sets.append("active=?")
            params.append(set_active)

        where = ["run_id=?", "stage=?", "stage_version=?", "active=1"]
        params += [run_id, expect_stage, expect_version]
        if expect_paid_gate is not None:
            where.append("paid_gate=?")
            params.append(expect_paid_gate)

        cur = self.conn.execute(
            f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE {' AND '.join(where)}",
            params,
        )
        self.conn.commit()
        return cur.rowcount == 1

    def set_status(self, run_id: str, status: str) -> None:
        """Status change WITHOUT a version bump (e.g. running_job → waiting_user
        after a step finishes — not a user action, must not invalidate buttons)."""
        self.conn.execute(
            "UPDATE pipeline_runs SET status=?, updated_at=? WHERE run_id=?",
            (status, _utcnow(), run_id),
        )
        self.conn.commit()

    def set_current_job(self, run_id: str, job_id: str, paid_gate: str) -> None:
        self.conn.execute(
            "UPDATE pipeline_runs SET current_job_id=?, paid_gate=?, updated_at=? WHERE run_id=?",
            (job_id, paid_gate, _utcnow(), run_id),
        )
        self.conn.commit()

    def record_notion_sync(self, run_id: str, ok: bool, notion_status: Optional[str] = None) -> None:
        """Best-effort Notion mirror result. On failure flag for later resync;
        a cron is deliberately deferred — `sync_notion_pending` script handles it."""
        if ok:
            self.conn.execute(
                "UPDATE pipeline_runs SET notion_sync_pending=0, notion_synced_at=?, "
                "notion_status=COALESCE(?, notion_status), updated_at=? WHERE run_id=?",
                (_utcnow(), notion_status, _utcnow(), run_id),
            )
        else:
            self.conn.execute(
                "UPDATE pipeline_runs SET notion_sync_pending=1, updated_at=? WHERE run_id=?",
                (_utcnow(), run_id),
            )
        self.conn.commit()

    # ── artifacts ─────────────────────────────────────────────────────────────
    def add_artifact(
        self,
        run_id: str,
        kind: str,
        *,
        path: Optional[str] = None,
        url: Optional[str] = None,
        text_content: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO pipeline_artifacts
               (run_id, kind, path, url, text_content, meta_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, kind, path, url, text_content,
             json.dumps(meta or {}, ensure_ascii=False), _utcnow()),
        )
        self.conn.commit()

    def get_artifacts(self, run_id: str) -> list[Artifact]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_artifacts WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [_row_to_artifact(r) for r in rows]

    # ── events (audit log) ─────────────────────────────────────────────────────
    def add_event(
        self,
        run_id: str,
        event_type: str,
        *,
        from_stage: Optional[str] = None,
        to_stage: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO pipeline_events
               (run_id, event_type, from_stage, to_stage, actor_user_id, payload_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, event_type, from_stage, to_stage, actor_user_id,
             json.dumps(payload or {}, ensure_ascii=False), _utcnow()),
        )
        self.conn.commit()

    def get_events(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_events WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        run_id=row["run_id"],
        tenant=row["tenant"],
        owner_user_id=row["owner_user_id"],
        plan=row["plan"],
        stage=row["stage"],
        status=row["status"],
        stage_version=row["stage_version"],
        active=row["active"],
        paid_gate=row["paid_gate"],
        actor_user_id=row["actor_user_id"],
        chat_id=row["chat_id"],
        notion_page_id=row["notion_page_id"],
        current_job_id=row["current_job_id"],
        notion_status=row["notion_status"],
        notion_synced_at=row["notion_synced_at"],
        notion_sync_pending=row["notion_sync_pending"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    meta = {}
    if row["meta_json"]:
        try:
            meta = json.loads(row["meta_json"])
        except (ValueError, TypeError):
            meta = {}
    return Artifact(
        id=row["id"],
        run_id=row["run_id"],
        kind=row["kind"],
        path=row["path"],
        url=row["url"],
        text_content=row["text_content"],
        meta=meta,
        created_at=row["created_at"],
    )

```

### pipeline_step_services.py
```python
"""Headless step services for the pipeline spine (bot-layer side).

These are the *real* ``StepRunner`` implementations the spine's EffectExecutor
calls — the production counterpart of ``content_pipeline.steps.MockStepRunner``.

Design rules (so the spine core stays pure and this stays portable):
  * this module does NOT ``import bot`` — importing bot.py would boot the whole
    Telegram app. Instead bot.py constructs ``BotStepRunner`` and INJECTS its
    own objects (the Claude client + brand-aware prompt resolvers);
  * services are HEADLESS — they take data, return artifacts, never send
    Telegram messages and never touch ``pending``/global bot state;
  * brand-awareness is honoured at call time via injected resolver callables, so
    the right system prompt is used without baking any client into this module.

Slice 1b ships script + cover generation. The avatar (paid) provider call stays
out: the 1b flow stops at the cost-gate, so ``start_paid_job`` raises until 1c
wires the real provider.
"""
from __future__ import annotations

from typing import Callable, Optional


class BotStepRunner:
    """Concrete StepRunner backed by the bot's Claude client + prompts.

    Parameters are injected by bot.py at startup:
      * ``claude_client``      — the Anthropic client (``.messages.create``);
      * ``script_system_fn``   — () -> str, brand-resolved script system prompt;
      * ``cover_system_fn``    — () -> str, brand-resolved cover system prompt;
      * ``script_model`` / ``cover_model`` — model ids;
      * ``force_shorten``      — optional (text) -> text post-processor.
    """

    def __init__(
        self,
        claude_client,
        *,
        script_system_fn: Callable[[], str],
        cover_system_fn: Callable[[], str],
        script_model: str = "claude-opus-4-7",
        cover_model: str = "claude-opus-4-7",
        force_shorten: Optional[Callable[[str], str]] = None,
        upload_audio_fn: Optional[Callable[[str], str]] = None,
        generate_fn: Optional[Callable[..., str]] = None,
    ) -> None:
        self.claude = claude_client
        self.script_system_fn = script_system_fn
        self.cover_system_fn = cover_system_fn
        self.script_model = script_model
        self.cover_model = cover_model
        self.force_shorten = force_shorten
        # Injected provider hooks (1c). When absent → start_paid_job stays a
        # no-go (1b behaviour). bot.py wires real HeyGen upload + generate here.
        self.upload_audio_fn = upload_audio_fn
        self.generate_fn = generate_fn

    # ── StepRunner protocol ───────────────────────────────────────────────────
    def generate_script(self, run_id: str, idea_text: str, config: dict) -> dict:
        resp = self.claude.messages.create(
            model=self.script_model,
            max_tokens=1024,
            system=self.script_system_fn(),
            messages=[{"role": "user", "content": idea_text}],
        )
        text = resp.content[0].text.strip()
        # Mirror bot.py: drop a leading "СЦЕНАРИЙ:" label if the model adds one.
        if text.upper().startswith("СЦЕНАРИЙ"):
            text = text.split("\n", 1)[-1].strip()
        if self.force_shorten is not None:
            text = self.force_shorten(text)
        return {"text_content": text, "meta": {}}

    def generate_cover_options(self, run_id: str, script_text: str, config: dict) -> dict:
        resp = self.claude.messages.create(
            model=self.cover_model,
            max_tokens=300,
            system=self.cover_system_fn(),
            messages=[{
                "role": "user",
                "content": (
                    f"Сценарий:\n{script_text}\n\nПридумай 5 вирусных текстов "
                    "для обложки. Каждый на новой строке, только текст, без нумерации."
                ),
            }],
        )
        raw = resp.content[0].text.strip()
        options = [
            ln.strip().strip('"').strip("«»").strip("-").strip()
            for ln in raw.split("\n") if ln.strip()
        ]
        options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]
        return {"meta": {"options": options}}

    def start_paid_job(self, run_id: str, kind: str, config: dict) -> str:
        """Submit the paid avatar render and return the provider job id.

        Headless: uploads the voice audio + submits generation, returns
        immediately (the render takes minutes — the poller tracks completion).
        Requires the provider hooks to be injected (1c); without them this is a
        no-go (1b safety).
        """
        if self.upload_audio_fn is None or self.generate_fn is None:
            raise NotImplementedError(
                "start_paid_job: provider hooks not injected (1b stops at the gate)"
            )
        audio_path = config.get("audio_path")
        if not audio_path:
            raise ValueError("start_paid_job: no voice audio for the run")
        audio_url = self.upload_audio_fn(audio_path)
        return self.generate_fn(
            audio_url,
            config.get("look_id"),
            config.get("avatar_version", "v3"),
        )

```

### bot_pipeline_adapter.py
```python
"""Telegram adapter for the pipeline spine — a HIDDEN, parallel entry point.

This is the bot-side translation layer:
  * Telegram update / callback  → ``PipelineEvent``
  * ``UIIntent``                → Telegram message + inline buttons
  * ``EffectCommand``           → executed via the injected ``BotStepRunner``

It runs ALONGSIDE the live bot (command ``/spine``), touching none of the
existing handlers. ``content_pipeline`` stays pure: this module imports it, not
the other way round. ``telegram`` is imported lazily inside handlers so the pure
codec/keyboard helpers (and their unit tests) work without telegram installed.

Slice 1b scope: idea → script → cover → voice(button stub) → avatar cost-gate.
No real provider call (the gate is the finish line; ``start_paid_job`` raises).
Real voice-message intake and the HeyGen call are 1c.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from content_pipeline.core import PipelineSpine, EffectExecutor, drive
from content_pipeline.models import (
    PipelineEvent,
    UIIntent, UIAction,
    EV_IDEA_RECEIVED, EV_APPROVE, EV_SKIP, EV_UPLOAD_VOICE, EV_CONFIRM_PAID,
    EV_OPEN_MATERIALS, EV_RESUME, EV_JOB_COMPLETED, EV_JOB_FAILED,
    UI_SHOW_RESULT,
    STAGE_VOICE, ST_WAITING_INPUT,
)
from content_pipeline.store import PipelineStore
from pipeline_step_services import BotStepRunner

logger = logging.getLogger("pipeline_adapter")

CB_PREFIX = "sp"

# Compact action codes — keep callback_data well under Telegram's 64-byte limit.
_ACTION_TO_CODE = {
    "approve": "a",
    "skip": "s",
    "upload": "u",
    "confirm_paid": "p",
    "open_materials": "m",
    "open_run": "o",
    "cancel": "c",
}
_CODE_TO_ACTION = {v: k for k, v in _ACTION_TO_CODE.items()}

# action → the PipelineEvent kind it produces
_ACTION_TO_EVENT = {
    "approve": EV_APPROVE,
    "skip": EV_SKIP,
    "upload": EV_UPLOAD_VOICE,
    "confirm_paid": EV_CONFIRM_PAID,
    "open_materials": EV_OPEN_MATERIALS,
    "open_run": EV_OPEN_MATERIALS,  # 1b: re-show current step's materials/status
}


# ── pure helpers (telegram-free → unit-testable) ────────────────────────────
def encode_action(a: UIAction) -> str:
    """UIAction → callback_data string ``sp:run:stage:ver:code``."""
    code = _ACTION_TO_CODE.get(a.action, "c")
    return f"{CB_PREFIX}:{a.run_id}:{a.stage}:{a.stage_version}:{code}"


def decode_cb(data: str) -> dict:
    """callback_data → {action, run_id, stage, stage_version}. Raises ValueError."""
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != CB_PREFIX:
        raise ValueError(f"bad spine callback: {data!r}")
    _, run_id, stage, ver, code = parts
    return {
        "action": _CODE_TO_ACTION.get(code, "cancel"),
        "run_id": run_id,
        "stage": stage,
        "stage_version": int(ver),
    }


def intent_to_keyboard_spec(intent: UIIntent) -> list[list[tuple[str, str]]]:
    """UIIntent → rows of (label, callback_data). Empty if no actions."""
    return [[(a.label, encode_action(a))] for a in intent.actions]


def intent_text(intent: UIIntent) -> str:
    head = (intent.title + "\n\n") if intent.title else ""
    return f"{head}{intent.body}".strip() or "…"


# ── module singletons (built by register_pipeline_spine) ────────────────────
_SPINE: PipelineSpine | None = None
_STORE: PipelineStore | None = None
_EXECUTOR: EffectExecutor | None = None
_STATUS_FN = None  # heygen_check_status(video_id) -> dict, injected for the poller
# Serializes ALL store access: drive() runs off the event loop (to_thread) so its
# blocking provider calls don't stall the live bot; the lock keeps the single
# SQLite connection used by one thread at a time.
_DRIVE_LOCK = threading.Lock()


def _ready() -> bool:
    return _SPINE is not None and _EXECUTOR is not None


def _drive_locked(event):
    """Run a spine event to completion under the global drive lock (sync)."""
    with _DRIVE_LOCK:
        return drive(_SPINE, _EXECUTOR, event)


async def _drive_async(event):
    """Drive off the event loop so blocking provider HTTP doesn't stall the bot."""
    return await asyncio.to_thread(_drive_locked, event)


async def _render(bot, chat_id, intents) -> None:
    """Send each UIIntent as its own Telegram message (+ inline buttons).

    UI_SHOW_RESULT carries the finished video — deliver it as a video by URL.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # lazy
    for intent in intents:
        spec = intent_to_keyboard_spec(intent)
        markup = None
        if spec:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(lbl, callback_data=cb) for (lbl, cb) in row]
                 for row in spec]
            )
        if intent.kind == UI_SHOW_RESULT and (intent.data.get("url") or intent.data.get("path")):
            try:
                src = intent.data.get("url") or intent.data.get("path")
                await bot.send_video(chat_id=chat_id, video=src,
                                     caption=intent_text(intent), reply_markup=markup)
                continue
            except Exception as e:
                logger.warning(f"[spine] send_video failed, falling back to text: {e}")
        await bot.send_message(chat_id=chat_id, text=intent_text(intent), reply_markup=markup)


async def poll_jobs(context) -> None:
    """Background poller: track submitted avatar renders → completion/failure.

    Runs on the job_queue. For each run with a pending provider job it asks the
    injected status fn; on completion/failure it feeds the spine the matching
    event and delivers the result to the run's chat. Never raises (a poll error
    must not kill the job queue)."""
    if not _ready() or _STATUS_FN is None or _STORE is None:
        return
    try:
        runs = _STORE.get_runs_awaiting_job()
    except Exception as e:
        logger.error(f"[spine] poll_jobs: store query failed: {e}", exc_info=True)
        return
    for run in runs:
        try:
            st = await asyncio.to_thread(_STATUS_FN, run.current_job_id)
        except Exception as e:
            logger.warning(f"[spine] status check failed run={run.run_id[:8]}: {e}")
            continue
        status = (st or {}).get("status")
        if status == "completed":
            ev = PipelineEvent(kind=EV_JOB_COMPLETED, run_id=run.run_id,
                               payload={"url": st.get("video_url"),
                                        "duration": st.get("duration")})
        elif status == "failed":
            ev = PipelineEvent(kind=EV_JOB_FAILED, run_id=run.run_id,
                               payload={"error": st.get("error")})
        else:
            continue  # still processing
        try:
            res = await _drive_async(ev)
            if run.chat_id:
                await _render(context.bot, int(run.chat_id), res.intents)
        except Exception as e:
            logger.error(f"[spine] poll_jobs deliver failed run={run.run_id[:8]}: {e}",
                         exc_info=True)


# ── handlers ────────────────────────────────────────────────────────────────
async def cmd_spine(update, context) -> None:
    """``/spine <идея>`` — start a new pipeline run on the parallel track."""
    if not _ready():
        await update.message.reply_text("Спайн ещё не инициализирован.")
        return
    idea = " ".join(context.args).strip() if getattr(context, "args", None) else ""
    if not idea:
        await update.message.reply_text(
            "Использование: /spine <идея ролика>\n"
            "(экспериментальный конвейер; на живой пайплайн не влияет)")
        return
    user_id = str(update.effective_user.id)
    ev = PipelineEvent(
        kind=EV_IDEA_RECEIVED, tenant="maksim",
        owner_user_id=user_id, actor_user_id=user_id,
        chat_id=str(update.effective_chat.id),
        payload={"idea_text": idea},
    )
    try:
        res = await _drive_async(ev)
    except Exception as e:  # never crash the live bot from the experimental track
        logger.error(f"[spine] cmd_spine drive failed: {e}", exc_info=True)
        await update.message.reply_text(f"Спайн: ошибка — {e}")
        return
    await _render(update.get_bot(), update.effective_chat.id, res.intents)


async def cmd_spine_resume(update, context) -> None:
    """``/spine_resume`` — list active runs and offer to continue."""
    if not _ready():
        await update.message.reply_text("Спайн ещё не инициализирован.")
        return
    user_id = str(update.effective_user.id)
    ev = PipelineEvent(kind=EV_RESUME, owner_user_id=user_id, actor_user_id=user_id,
                       chat_id=str(update.effective_chat.id))
    res = await _drive_async(ev)
    await _render(update.get_bot(), update.effective_chat.id, res.intents)


async def on_spine_voice(update, context) -> None:
    """A voice message while a spine run awaits voice → upload_voice with the
    real downloaded audio. Only reached when ``_SpineAwaitingVoiceFilter`` passed,
    so the live ``process_voice`` is untouched for everything else."""
    from pathlib import Path as _Path
    user_id = str(update.effective_user.id)
    runs = [r for r in _STORE.get_active_runs(user_id)
            if r.stage == STAGE_VOICE and r.status == ST_WAITING_INPUT]
    if not runs:
        return
    run = runs[0]
    media_dir = _Path(_STORE.db_path).parent / "spine_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / f"{run.run_id}_voice.ogg"
    try:
        tg_file = await update.message.voice.get_file()
        await tg_file.download_to_drive(str(dest))
    except Exception as e:
        logger.error(f"[spine] voice download failed: {e}", exc_info=True)
        await update.message.reply_text("Спайн: не удалось скачать голосовое.")
        return
    ev = PipelineEvent(
        kind=EV_UPLOAD_VOICE, tenant="maksim", owner_user_id=user_id,
        actor_user_id=user_id, chat_id=str(update.effective_chat.id),
        run_id=run.run_id, stage=run.stage, stage_version=run.stage_version,
        payload={"audio_path": str(dest)},
    )
    try:
        res = await _drive_async(ev)
    except Exception as e:
        logger.error(f"[spine] voice drive failed: {e}", exc_info=True)
        await update.message.reply_text(f"Спайн: ошибка — {e}")
        return
    await _render(update.get_bot(), update.effective_chat.id, res.intents)


async def on_spine_callback(update, context) -> None:
    """Inline-button presses with the ``sp:`` prefix."""
    query = update.callback_query
    await query.answer()
    try:
        parsed = decode_cb(query.data)
    except ValueError:
        return
    user_id = str(update.effective_user.id)
    ev = PipelineEvent(
        kind=_ACTION_TO_EVENT.get(parsed["action"], EV_OPEN_MATERIALS),
        tenant="maksim", owner_user_id=user_id, actor_user_id=user_id,
        chat_id=str(query.message.chat_id),
        run_id=parsed["run_id"], stage=parsed["stage"],
        stage_version=parsed["stage_version"],
        payload={"audio_path": None} if parsed["action"] == "upload" else {},
    )
    try:
        res = await _drive_async(ev)
    except Exception as e:
        logger.error(f"[spine] callback drive failed: {e}", exc_info=True)
        await query.get_bot().send_message(chat_id=query.message.chat_id,
                                           text=f"Спайн: ошибка — {e}")
        return
    await _render(query.get_bot(), query.message.chat_id, res.intents)


def register_pipeline_spine(
    application,
    *,
    claude_client,
    script_system_fn,
    cover_system_fn,
    db_path: str,
    script_model: str = "claude-opus-4-7",
    cover_model: str = "claude-opus-4-7",
    force_shorten=None,
    heygen_upload_fn=None,
    heygen_generate_fn=None,
    heygen_status_fn=None,
    poll_interval: int = 20,
) -> None:
    """Wire the hidden spine track into the live bot. Called once at startup.

    Dependency direction: bot.py → this adapter → content_pipeline. The Claude
    client, brand-aware prompt resolvers and HeyGen hooks are injected so neither
    this module nor the core imports bot.py.

    If the HeyGen hooks are omitted the track still runs up to the cost-gate
    (1b behaviour); with them, ``confirm_paid`` submits a real render and the
    poller delivers the finished video (1c).
    """
    global _SPINE, _STORE, _EXECUTOR, _STATUS_FN
    from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler  # lazy
    from telegram.ext import filters as _filters

    _STORE = PipelineStore(db_path)
    runner = BotStepRunner(
        claude_client,
        script_system_fn=script_system_fn,
        cover_system_fn=cover_system_fn,
        script_model=script_model,
        cover_model=cover_model,
        force_shorten=force_shorten,
        upload_audio_fn=heygen_upload_fn,
        generate_fn=heygen_generate_fn,
    )
    _SPINE = PipelineSpine(_STORE)
    _EXECUTOR = EffectExecutor(_STORE, runner)
    _STATUS_FN = heygen_status_fn

    # Voice intake — a custom filter that matches ONLY when the sender has an
    # active spine run awaiting voice. Registered in group 0 BEFORE the live
    # process_voice (one handler per group runs), so ordinary voice messages
    # fall through to the live flow untouched. Ultra-defensive: any error → False.
    class _SpineAwaitingVoiceFilter(_filters.MessageFilter):
        def filter(self, message) -> bool:
            try:
                if _STORE is None or message.from_user is None:
                    return False
                runs = _STORE.get_active_runs(str(message.from_user.id))
                return any(r.stage == STAGE_VOICE and r.status == ST_WAITING_INPUT
                           for r in runs)
            except Exception:
                return False

    application.add_handler(
        MessageHandler((_filters.VOICE | _filters.AUDIO) & _SpineAwaitingVoiceFilter(),
                       on_spine_voice)
    )
    application.add_handler(CommandHandler("spine", cmd_spine))
    application.add_handler(CommandHandler("spine_resume", cmd_spine_resume))
    application.add_handler(CallbackQueryHandler(on_spine_callback, pattern=r"^sp:"))

    # Background poller for async provider renders (only if status hook present).
    if heygen_status_fn is not None and getattr(application, "job_queue", None):
        application.job_queue.run_repeating(poll_jobs, interval=poll_interval, first=poll_interval)
        logger.info(f"[spine] job poller registered (every {poll_interval}s)")

    logger.info(f"[spine] hidden pipeline track registered (db={db_path})")

```
