# content_pipeline — CONTRACT (для конструктора / Тёмы)

> Это **контракт ядра**, а не «как получилось». Строй конструктор от него, а не
> от diff-а монолита. Slice 1a реализован и покрыт unit-тестами (на моках, без
> провайдеров). Расположение: `content_pipeline/` (пакет), `docs/pipeline_spine/`.
>
> Запуск тестов: `python -m unittest content_pipeline.tests.test_spine -v`

## Зачем
Единый «спайн» пайплайна «идея → ролик», вынесенный из `bot.py` отдельным
**параллельным треком**. Ядро **tenant-agnostic** и **transport-agnostic**:
сегодня им рулит Telegram-адаптер, завтра — Mini App (второй адаптер, без
переписывания ядра).

## Направление зависимостей (жёстко)
```
bot.py / bot_pipeline_adapter (1b)  →  content_pipeline
content_pipeline  →  НИЧЕГО из bot-слоя
```
Проверяется тестами `test_core_has_no_bot_imports`, `test_core_has_no_tenant_constants`.

## Вход: PipelineEvent
```python
PipelineEvent(
  kind,            # idea_received | approve | skip | upload_voice | confirm_paid | open_materials | resume | step_completed(internal)
  tenant, owner_user_id, actor_user_id, chat_id,
  run_id, stage, stage_version,   # action-события несут stage+stage_version для CAS
  notion_page_id,
  payload,         # dict (idea_text, audio_path, plan, ...)
)
```

## Выход: два чистых потока
1. **UIIntent** — *что показать* (transport-agnostic; адаптер решает send/edit/screen):
```python
UIIntent(kind, run_id, title, body, actions:[UIAction], fields:[InputField], data)
# kind: show_step | show_resume_list | request_input | show_cost_gate |
#       show_status | show_materials | show_stale_state | show_error
UIAction(label, action, run_id, stage, stage_version, style)
# action: approve | skip | upload | confirm_paid | open_materials | cancel | open_run
# style:  default | primary | paid | danger | secondary
```
2. **EffectCommand** — *что сделать* (в т.ч. платное), с `idempotency_key`:
```python
EffectCommand(kind, run_id, payload, idempotency_key)
# kind: generate_script | generate_cover_options | start_paid_provider_job |
#       update_notion_status | build_materials_zip
```
Адаптер исполняет UIIntent (рисует), а EffectCommand отдаёт в `EffectExecutor`
(который зовёт headless `StepRunner`). Цикл «событие → эффекты → follow-up
события» — `core.drive(spine, executor, event)`.

## StepRunner (headless — НЕ шлёт Telegram, НЕ трогает pending)
```python
class StepRunner(Protocol):
    def generate_script(run_id, idea_text, config) -> {"text_content","meta"}
    def generate_cover_options(run_id, script_text, config) -> {"meta":{"options":[...]}}
    def start_paid_job(run_id, kind, config) -> job_id
```
1a использует `MockStepRunner`. 1b: вынуть чистые `ScriptService`/`CoverService`
из `bot.py` (старый хендлер потом тоже может ими пользоваться).

## Store (SQLite — runtime-правда; Notion — зеркало)
Таблицы: `pipeline_runs`, `pipeline_artifacts`, `pipeline_events` (audit-log,
не event-sourcing). Ключевой примитив — `cas_transition(...)`: атомарный
compare-and-swap по `(stage, stage_version)`; он же advance, он же stale-guard,
он же идемпотентность платного шага. `notion_page_id` — родитель карточки,
`run_id` — конкретный прогон производства (у карточки их может быть несколько).

## 10 жёстких инвариантов (закон)
1. Core не импортирует Telegram / `bot.py`.
2. Core не содержит клиент-имён, avatar_id, Notion-статусов, текстов под клиента.
3. Runtime-правда — SQLite.
4. Notion — зеркало (best-effort; при сбое `notion_sync_pending=1`).
5. Любая смена состояния — через `stage_version` (CAS).
6. Платный эффект нельзя создать без `paid_confirmed`.
7. Повторный `confirm_paid` не создаёт второй платный эффект (идемпотентность).
8. `skip` — явное событие в логе (`stage_skipped`), а не дыра в данных.
9. UI-адаптер не принимает бизнес-решений.
10. StepRunner не шлёт Telegram-сообщения.

## Что в 1a НЕ сделано (следующие срезы)
Telegram-адаптер (1b) + извлечение реальных шагов; реальный HeyGen-вызов;
планы selfie / broll-only + каскад пропусков; публикация; Mini App-адаптер;
cron-досинк Notion (пока — ручной скрипт). Текущий слайс доходит до
**avatar cost-gate** и доказывает: после рестарта/`/start` run восстановим, а
платный job нельзя запустить случайно или дважды.
