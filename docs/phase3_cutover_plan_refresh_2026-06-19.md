# Phase 3 Cutover — актуализация плана (19 июня 2026)

**Что это:** дельта-ревизия плана от 16 июня (`phase3_precutover_risk_2026-06-16.md`) под текущее состояние кода. НЕ замена — опирается на существующие docs (risk-doc, `cto_review_phase3_cutover_2026-06-16.md`, `DEPLOY_RUNBOOK.md`, `MIGRATION_STATE_INVENTORY.md`). Основано на аудите кода 19 июня (4 агента, run `wf_5a72b9dc-e06`).

**Цель cutover:** panferov (бот Артёма «Илон») переезжает с legacy `content-bot-2` на ядро `maksim-bot` (CORE) через `tenant.json`. После свитча panferov получает разом ВСЕ фичи ядра, включая свежие: **Seedance (AI-видео), B-roll-без-аватара (Pipeline 2), HyperFrames, Remotion** — без поштучного порта в legacy.

**Статус:** ChatGPT-ревью этого плана пройдено 19 июня (вердикт **GO-1 / parity cutover**); 7 находок (C3-C7, I1/I2/I4/I6) внесены ниже. Стратегия по решениям Артёма: свитч на паритете (движки OFF) → движки по одному в срезе C.

---

## 1. Что изменилось с 16 июня (почему нужна актуализация)

1. **Фаза 2 уже выполнена в коде** (расхождение docs↔код). Команды `/update /report /launches /brand` зарегистрированы per-tenant через feature-флаги (`bot.py:22013-22034`), но risk-doc (стр. 41-43) и `cutover_doctor.EXPECTED_COMMANDS` (`tools/cutover_doctor.py:34`) всё ещё описывают их как «закомментированы под Максима». → **обновить docs + cutover_doctor**, иначе доктор даст ложный сигнал.

2. **Появились 4 группы новых фич** (13-18 июня, все в core, в legacy НЕТ) с НОВЫМИ серверными зависимостями, которые Фаза 1 не учитывала:
   - **Seedance** (`ai_video_broll.py`, `fal_media.py:46`) → нужен `FAL_KEY` (fal.ai).
   - **B-roll-без-аватара / Pipeline 2** (`broll/` — assembler/draft/handlers/source_menu) → чистый Python, приедет с кодом ядра, спец-инфры нет.
   - **HyperFrames** (`hyperframes_broll.py`) → `chrome-headless-shell` + CLI `hyperframes@0.6.56` + fonts + проект `hyperframes-broll/`.
   - **Remotion** (`auto_broll.py`) → Node-проект `panferov-broll/` на сервере (НЕ в git) + `npx remotion render`.

3. **Активация HF/Remotion под бренд panferov** (Артём 19 июня: нужны ОБА движка). Узкие места — раздел 4 ниже.

4. **Пробел во флагах:** `_KNOWN_FEATURES` (tenant.py:57-70) НЕ содержит флагов для Seedance/Pipeline-2 (добавлены позже tenant-модели). Сейчас они либо ядро (вкл всегда), либо не гейтятся. → решить в Фазе 3 (раздел открытых вопросов).

---

## 2. Актуальный статус Фаз 0-6

| Фаза | Статус | Комментарий |
|---|---|---|
| **0 — доразведка сервера** | ✅ ГОТОВО | DEPS-блокер снят (venv Артёма укомплектован); billing.db = тестовый Максим (НЕ переносить); OAuth-токены известны; cron нет |
| **Защитный слой** (между 0 и свитчем) | ✅ ГОТОВО | cutover_doctor / pending_migrator / cutover_snapshot / rollback --restore-state / cutover_status — всё под TDD |
| **1 — инфра B-roll** | ❌ НЕ СДЕЛАНО | + РАСШИРЕНА новыми фичами (см. раздел 3.1) |
| **2 — команды per-tenant** | ✅ ГОТОВО | сделано в коде; docs/doctor устарели → поправить |
| **3 — конфиг/env + tenant.json** | ❌ НЕ СДЕЛАНО | + активация HF/Remotion + новые флаги (3.2) |
| **4 — перенос state** | ❌ НЕ СДЕЛАНО | rsync ~1.7G в окно простоя (3.3) |
| **5 — gate перед свитчем** | ❌ НЕ СДЕЛАНО | doctor green + смок тест-токен + rehearsal rollback (3.4) |
| **6 — боевой свитч** | ❌ НЕ СДЕЛАНО | по DEPLOY_RUNBOOK + watch 15 мин (3.5) |

---

## 3. Детальный план оставшегося

### 3.1 Фаза 1 — серверная инфра (РАСШИРЕНА)

Ставится на сервер Артёма `65.21.154.237` в окружение core (`/root/contentbot-core`). Бот стартует без этого (ленивый импорт), но B-roll/HF/Remotion/Seedance падают при вызове.

**Базовое (из risk-doc B1-B4):**
- Node ≥ 22.12, `npm install`, puppeteer.
- Claude Code CLI + `CLAUDE_CODE_OAUTH_TOKEN` (Max-подписка) — общий gen-flock для HF/Remotion/Seedance-режиссёра.

**Seedance:**
- `FAL_KEY` в `.env` (fal.ai). Python-зависимость `fal_client` (проверить в requirements ядра).

**HyperFrames:**
- `chrome-headless-shell` (НЕ snap-chromium) + `HYPERFRAMES_BROWSER_PATH`.
- CLI `hyperframes@0.6.56` (пин `HF_VERSION`), node_modules.
- Проект `hyperframes-broll/` (env `HYPERFRAMES_PROJECT_DIR`): `index.html`-образец + `fonts/` (6 woff2 Inter Tight/Inter). Забрать рабочий с сервера Максима `89.167.89.133:/home/maksim-bot/hyperframes-broll`, перенастроить путь под Артёма.
- ffmpeg (уже есть на сервере).

**Remotion:**
- Node-проект `panferov-broll/` (env `BROLL_PROJECT_DIR`): `src/scenes/*.tsx` + `fonts.ts` + `Root.tsx` + `package.json` с `remotion`. Забрать с `89.167.89.133:/home/maksim-bot/panferov-broll` (в git НЕТ). Под panferov — перекрасить (раздел 4).

### 3.2 Фаза 3 — конфиг/env + боевой tenant.json

**tenant.json (боевой, из panferov.example.json + правки):**
- ⚠️ **На боевом свитче (паритет, Q2): `hyperframes=false, remotion=false, ai_video=false, broll_pipeline=false`.** Движки включаются по одному в срезе C (раздел 4), НЕ на свитче. (В panferov.example.json сейчас hyperframes/remotion=false — для свитча правильно; ai_video/broll_pipeline появятся после флаг-аудита, тоже false на свитче.)
- 🔴 **billing — снять противоречие (C6, ChatGPT):** план раньше держал `billing=true`, но база на core пустая (Максим не переносится) → риск нулевого баланса / блокировки владельца / ложных записей в первые минуты. **РЕШЕНИЕ: на cutover `billing=false`** (или `true` ТОЛЬКО при подтверждённом owner-bypass — Артём в `ADMIN_TELEGRAM_IDS`, проверить), а `cutover_doctor` проверяет, что в billing.db нет `lifedrive/maksim`. Дефолт = `false` на свитче, включить отдельным шагом после стабилизации.
- ✅ `subscriber_stats` УЖЕ в `_KNOWN_FEATURES` (tenant.py:69) — не unknown-флаг, doctor не ругнётся (ответ на I7).
- Остальные флаги panferov как есть (tg_post/launch_monitor/youtube_broll/image_gen/video_gen/instagram_dm/subscriber_stats = true; carousel/idea_bank = false).
- `brands.allowed = [default, shoes]`, `brand_overrides.default` (HEYGEN_AVATAR_ID/ELEVENLABS_VOICE_ID из env).

**env (из risk-doc D/E/R):**
- `TENANT_STRICT=1`, `TENANT_ID_EXPECTED=panferov`, `TENANT_CONFIG=<путь к боевому tenant.json>`.
- `META_APP_ID/META_APP_SECRET` (+ ротация — были в git plaintext, G2), `INSTAGRAM_WEBHOOK_VERIFY_TOKEN`.
- `DEFAULT_BRAND` unset.
- Пути R4 под сервер Артёма; crosspost R5 (`INSTAGRAM_TARGET_USERNAME` сейчас хардкод `@livedrive_karting` → канал Артёма).
- Боевой `notion_db_id` Артёма (G3).
- `FAL_KEY`, `HYPERFRAMES_PROJECT_DIR`, `BROLL_PROJECT_DIR`, `HYPERFRAMES_BROWSER_PATH` (из 3.1).

### 3.3 Фаза 4 — перенос state (окно простоя)

🔴 **C5 (ChatGPT): pre-rsync ДО простоя + final delta В простой** — не один большой rsync 1.7G в окно простоя (иначе downtime непредсказуем). Разделить:
- **ДО downtime (бот ещё работает):** `rsync -aH` крупных почти-неизменных директорий `projects/`(927M)+`assets/`(118M)+`broll-library/`(559M)+`music/`(136M) ≈ 1.7G — основной объём переезжает без простоя.
- **В downtime (боты остановлены):** `rsync -aH --delete` тех же директорий = только дельта (быстро) + state-файлы ниже.

State (в downtime, из `MIGRATION_STATE_INVENTORY.md`):
- OAuth + `telethon_session.session` — копия при остановленном процессе. ⚠️ IG-токен истекает ~60 дней с 30 апр (≈29 июня) — проверить/освежить ДО (I3).
- `pending.json` → через `pending_migrator` (allowlist) либо RESET. 🔴 **I6 (ChatGPT): обновить allowlist `pending_migrator` под НОВЫЕ state-поля** (Seedance/`ai_video`, Pipeline-2 draft, cover-gate, narrative) — allowlist писался 16 июня, новых полей не знает → прогнать на pending с искусственными новыми полями ДО Фазы 5.
- `stats_history.json` / `pub_calendar.json`.
- billing.db — НЕ переносить (тестовый Максим); core стартует с пустой базы. cutover_doctor флагует `lifedrive` как чужого → очистить.
- venv пересоздаётся; `/srv/*` симлинки остаются.

### 3.4 Фаза 5 — gate перед свитчем

- `python -m tools.cutover_doctor` зелёный НА сервере в окружении core + readiness OAuth.
  - 🔴 **C4 (ChatGPT): doctor выводит PER-TENANT** (`expected/registered/gated` по фактическому tenant.json), не «команда есть/нет вообще» — иначе ложные сигналы убивают доверие к gate.
  - 🔴 **C3/C7 anti-leakage в doctor:** при `tenant_id=panferov` grep активных prompt/reference/scene-файлов на `livedrive|karting|glamping|#ff5722|maksim|Постулат` → любой хит = NO-GO (защита от протечки бренда Максима в ролики Артёма).
- Смок на тест-токене: doctor strict green → /cards → /balance → фичи → /brand (только default+shoes, leakage-route).
- 🟡 **I1 (ChatGPT): smoke включённых движков = readiness-gate с артефактом** (не «просто прогон»). Для каждого ВКЛЮЧЁННОГО движка зафиксировать: enabled/env_ok/provider_ok/output_file_exists/duration/size/cost_logged (Seedance), browser_ok/style_contract=panferov/render (HF), node_ok/brand_grep_ok/render (Remotion). Readiness false при включённой фиче = NO-GO. На паритетном свитче движки OFF → этот гейт работает в срезе C.
- **Прогнать `rollback_panferov.sh` на тест-токене ДО боевого свитча** — 🟡 **I6: rehearsal включает сценарий «после нового B-roll/Seedance draft»** (новые state-поля).

### 3.5 Фаза 6 — боевой свитч

По `DEPLOY_RUNBOOK.md` §Phase 3: flock token-lock + `Conflicts=` + unit-level stop старого (НЕ pgrep — на сервере ещё Nox) → start core боевым токеном → смок → post-start watch 15 мин, rollback наготове 1-2 ч.

---

## 4. Бренд-адаптация HF/Remotion под panferov (узкие места)

> По решению Q2 (19 июня) это ОТДЕЛЬНЫЙ срез ПОСЛЕ паритетного свитча (Фаза 6), не часть боевого свитча. Флаги hyperframes/remotion/ai_video включаются здесь, не на свитче.

**HyperFrames:**
- ✅ Контракт готов: `hyperframes_assets/style_contract.panferov.json` (Nox Dark, тест `test_style_contract_panferov.py` PASS).
- 🔴 `load_style_contract()` зовётся БЕЗ пути (`hyperframes_broll.py:364` и `:558`) → всегда грузит дефолтный (оранжевый Максима). Нужно прокинуть выбор контракта per-tenant (env `HF_STYLE_CONTRACT_PATH` или через `tenant.py`).
- 🔴 `hyperframes_assets/reference_pack.md` хардкодит палитру Максима (`:11` accent #FF5722, `:124` «оранж»). Этот файл инлайнится Клоду наравне с контрактом → нужен `reference_pack.panferov.md` (или параметризация accent), иначе модель получит конфликт azure-vs-orange.

**Remotion:**
- 🔴 Бренд захардкожен в промпт `auto_broll.py:70, 95-96`: «студия Постулат», `#0a0a0a`/`#ff5722`, Inter Tight, контекст «картинг+глэмпинг Life Drive Тюмень». Параметризовать под panferov (AI-студия/личный бренд + палитра Nox Dark).
- 🔴 Эталон `src/scenes/MaksimInserts2.tsx` + `fonts.ts` на сервере — перекрасить `colors`, переписать визуальный канон под айдентику panferov (ручная дизайн-работа на сервере, не в git).
- Статус движка: Remotion «стоит перепроверить прогоном» (HF — «проверен в проде», PIPELINES_OVERVIEW.md:128-129). При cutover — обязательный тест-прогон Remotion на сервере Артёма.

**Процесс активации движков (срез C, после паритетного свитча) — правки ChatGPT:**
- 🔴 **C7: проекты `hyperframes-broll/`/`panferov-broll` с сервера Максима копировать как TEMPLATE, не как боевой проект:** удалить `node_modules` → чистый `npm ci` → grep на `maksim|livedrive|karting|glamping|#ff5722` + пути/секреты сервера Максима → заменить style_contract/reference_pack/scene-эталон → один controlled render на тест-токене. Иначе занесём чужой бренд/секреты/клиентские assets.
- 🔴 **C1: включать движки ПО ОДНОМУ** (не пачкой): `broll_pipeline` → `ai_video` → `hyperframes` → `remotion`. Каждый: флаг=true → smoke (readiness-артефакт I1) → watch → при сбое выключить флаг (де-факто kill-switch без деплоя — правка tenant.json + рестарт).
- 🟡 **I4: cost-guard для Seedance** (платный fal.ai, money-leak): `AI_VIDEO_MAX_CLIPS_PER_RUN`, `AI_VIDEO_MAX_DURATION`, дневной счётчик/бюджет в json/log, `owner_only` до первого клиента, лог estimated cost per run. Часть provisioning Seedance.
- 🟡 **I5: единый lock на Claude Code CLI/OAuth** для HF+Remotion+Seedance-режиссёра (общий gen-flock `claude_gen_lock.py` — проверить, что реально один на все 3, не по модулю): иначе одновременный запуск упрётся в лимиты Max. + видимый «генератор занят, в очереди» + timeout/cancel + лог `queue_wait_sec`.

---

## 5. Решения Артёма (19 июня) + ротация

1. **Флаги — РЕШЕНО: добавить флаги ВЕЗДЕ (флаг-аудит) + provisioning к каждому.** Продуктовая стратегия: контент-бот продаётся КОНСТРУКТОРОМ отдельных функций (не «всё включено») — клиент берёт только нужное (только HF / только Seedance / селфи+аватар+Seedance). Поэтому:
   - **Аудит:** каждая фича обязана иметь feature-флаг. Добавить недостающие в `_KNOWN_FEATURES` (на 19 июня без флагов: `ai_video`/Seedance, `broll_pipeline`/Pipeline-2; проверить остальные свежие фичи) + гейт во все точки (меню/команда/handler).
   - **Provisioning-matrix:** к каждому флагу прописать, что требуется от клиента (VK-ключи, оплата/аккаунт fal.ai для Seedance, HeyGen/ElevenLabs, Meta-секреты) и чьи ключи / кто платит (клиент или студия с переставлением биллинга).
   - Деталь стратегии → память `project_contentbot_constructor_model.md`. Для panferov: новые флаги = true.
2. **Порядок активации движков — РЕШЕНО: сначала паритет, потом движки.** Боевой свитч (Фаза 6) — на голом паритете (минимум точек отказа). HF/Remotion/Seedance — ОТДЕЛЬНЫМ срезом сразу после успешного свитча + бренд-адаптация (раздел 4). Это снижает риск Фазы 6.
3. **Ротация секретов — РЕШЕНО: ТОЛЬКО ДО окна, не во время (I2, ChatGPT).** META_APP_SECRET (git plaintext, G2), Gemini web-search ключ (светился 18 июня), при необходимости FAL_KEY. За день до боевого окна: ротировать → обновить `.env` → smoke тестового контура → зафиксировать «credential readiness». Ротация в окне свитча запрещена (при падении не отличить миграцию от нового секрета).

---

## 6. Что НЕ входит / backlog
- «Доделать billing Артёма» — backlog (сейчас выключен на core, тестовый Максим вычистить).
- Carousel / idea_bank для panferov — Артём не просил (флаги false).

---

## 7. Gate: внешнее ревью
- 7-агентный risk-assessment + ChatGPT-ревью базового плана — сделаны 16 июня, находки закрыты защитным слоем.
- **ChatGPT-ревью ЭТОГО обновлённого плана — СДЕЛАНО 19 июня** (`C:\Users\Dell\Downloads\cto_review_phase3_cutover_refresh_2026-06-19.md`, вердикт CONDITIONAL GO / GO-1 parity). Все 7 принятых находок (C3-C7, I1/I2/I4/I6) внесены в этот документ. Отклонено/уточнено: I7 (subscriber_stats уже во флагах), общий тон «major release» (мы идём паритетом → его же GO-1).
- **Перед Фазой 6** — финальный preflight по GO-checklist (раздел 9, из ревью), повторное полноценное ревью не требуется.

---

## Порядок исполнения (по решениям 19 июня: ПАРИТЕТ → потом ДВИЖКИ)

### A. Подготовка (код + решения, до сервера)
1. Поправить docs↔код расхождение Фазы 2 (`cutover_doctor.EXPECTED_COMMANDS` + risk-doc) — снимает ложный сигнал доктора. + **doctor per-tenant вывод (C4) + anti-leakage grep (C3/C7)**. TDD.
2. **Флаг-аудит** (раздел 5.1): добавить недостающие флаги (`ai_video`/Seedance, `broll_pipeline`/Pipeline-2 + проверить остальные свежие фичи) в `_KNOWN_FEATURES` + гейт во все точки + черновик provisioning-matrix. TDD.
3. **Решить billing (C6):** `false` на cutover или owner-bypass — снять противоречие (раздел 3.2).
4. **Обновить `pending_migrator` allowlist под новые state-поля (I6)** + тест. TDD.
5. **Ротация секретов ДО окна (I2):** Meta/Gemini/FAL → `.env` → smoke → credential readiness.

### B. Cutover на ПАРИТЕТЕ (новые движки/фичи пока OFF)
3. Фаза 1-базовая (Node/Claude CLI/puppeteer — БЕЗ инфры HF/Remotion/Seedance).
4. Фаза 3 (боевой tenant.json: hyperframes/remotion/ai_video/broll_pipeline = **false** на этом этапе + env + ротация секретов).
5. Фаза 4 (перенос state, окно простоя).
6. Фаза 5 (gate: doctor green + смок тест-токен + rehearsal rollback).
7. ChatGPT-ревью готовности → Фаза 6 (боевой свитч на паритете + watch 15 мин).

### C. Движки/конструктор — отдельным срезом ПОСЛЕ успешного свитча
8. Бренд-адаптация в коде (раздел 4): `load_style_contract` выбор panferov-контракта + `reference_pack.panferov.md` + параметризация Remotion-промпта. TDD.
9. Серверная инфра движков (3.1): `FAL_KEY` (Seedance), chrome-headless-shell+CLI+`hyperframes-broll` (HF), `panferov-broll` Node-проект (Remotion).
10. Включить флаги `hyperframes/remotion/ai_video = true` в боевом tenant.json + тест-прогон каждого движка на сервере Артёма (Remotion обязательно — был «не проверен»).

---

## 9. GO-checklist перед Фазой 6 (паритетный свитч) — из ревью

- [ ] `cutover_doctor` обновлён (per-tenant + anti-leakage grep) и green.
- [ ] `TENANT_STRICT=1`, `TENANT_ID_EXPECTED=panferov`, `DEFAULT_BRAND` unset.
- [ ] billing решён: `false` на свитче ИЛИ owner-bypass подтверждён; billing.db без `lifedrive/maksim`.
- [ ] `ai_video`/`broll_pipeline` добавлены в `_KNOWN_FEATURES`; на свитче `hyperframes/remotion/ai_video/broll_pipeline = false`.
- [ ] IG-токен освежён (истекает ~29 июня).
- [ ] Meta/Gemini секреты ротированы ДО окна; credential readiness зафиксирован.
- [ ] Pre-rsync выполнен; final-delta команда готова.
- [ ] `pending_migrator` протестирован на новых state-полях.
- [ ] `rollback_panferov.sh --restore-state` rehearsal пройден (вкл. сценарий с новым B-roll draft).
- [ ] Смок на тест-токене пройден.
- [ ] Старый unit сохранён (≥ неделю после свитча).
- [ ] Watch-owner назначен (15 мин паритет; 60-120 мин в срезе C при движках).
