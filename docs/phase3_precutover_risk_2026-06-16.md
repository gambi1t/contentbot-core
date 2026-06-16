# Phase 3 pre-cutover risk assessment (16 июня 2026)

> Многоагентный дифф `panferov-legacy` (бот Артёма, `content-bot-2`) ↔ `core` (main = `maksim-bot`) перед боевой пересадкой. 5 осей + синтез + адверсариальный критик (7 агентов). Этот файл — рабочий чек-лист cutover.

## Вердикт: **NO-GO на прямую пересадку. GO после закрытия блокеров ниже.**

**Хорошая новость:** core — это **надмножество** legacy (форк + расширения), 21951 vs 15161 строк. **Ни одна фича Артёма не потеряна в коде.** Все «блокеры» = конфиг / инфра / регистрация, НЕ отсутствие кода. После их закрытия пересадка на полный паритет безопасна.

⚠️ **Сервер Артёма = `65.21.154.237`** (Hetzner, content-bot + Nox). Агент deps-оси ошибочно указал `89.167.89.133` — это сервер Максима. Все «доустановить на сервер A» относятся к **65.21.154.237**.

---

## 🔴 Критичные находки адверсариального критика (могли сломать прод)

1. **ДЫРА #1 — billing.db в core = база МАКСИМА.** `maksim-bot/billing/billing.db` байт-в-байт = legacy-файл, но единственный клиент в нём = `Максим/lifedrive`. Наивный «перенести billing.db» **затёр бы боевой billing Артёма или занёс чужого клиента**. → НЕ переносить вслепую. На сервере: `SELECT * FROM clients` в боевой базе Артёма, переносить именно ЕГО `billing/billing.db`. (Корневой `maksim-bot/billing.db` — пустой dev-мусор.)
2. **ДЫРА #2 — TENANT_STRICT = наш защитный рычаг (уже реализован, Phase 2c-1).** `TENANT_STRICT=1 + TENANT_ID_EXPECTED=panferov` → без правильного tenant.json бот **не стартует** (FATAL), вместо тихой утечки к Максиму. Cutover делать ТОЛЬКО со strict=1 — превращает D1/R3/R5 из «молчаливая утечка» в «громкий отказ старта».
3. **ДЫРА #3 — requirements расходятся на 6 пакетов, не 1.** core добавил: `elevenlabs`, `faster-whisper`, `httpx`, `requests-toolbelt`, `opencv-python-headless`, `python-telegram-bot[job-queue]` (extra!). Большинство уже стоят на сервере Артёма (legacy импортит на старте), но **`[job-queue]` extra** (APScheduler) и `opencv` проверить. Без job-queue — launch_monitor cron молча не поднимется. → `pip freeze` на сервере обязателен.

## ⚠️ Что критик добавил (упущено основным ассессментом)
- **G1 — OAuth-токены соцсетей + Telethon .session НЕ в .env.** refresh-токены YouTube/IG/VK хранятся в файлах/БД на сервере + `telethon_uploader.py` .session. В переносе их НЕТ — без них Артём разлогинен на всех площадках. Добавить в перенос.
- **G2 — META_APP_SECRET Артёма в plaintext в git** (`content-bot-2/crosspost.py:34`). Скомпрометирован нахождением в репо — проверить/отозвать, не просто перенести.
- **G3 — Notion-схема Артёма** проверить на живой базе (не на стале диффа) + вписать боевой `notion_db_id` в panferov tenant.json.
- **G4 — системные cron/systemd Артёма** под legacy (launch_monitor дайджест и пр.) — инвентаризировать.
- **G5 — активные сессии в момент cutover.** pending Артёма содержит `notion_edit_card/notion_edit_title/shotlist` (активное редактирование) — расширить список «эфемерных для вычистки» (не только `selfie_*/stats_draft/voice_parts`).

---

## Блокеры (закрыть до cutover)

### A. Инфра B-roll (бот СТАРТУЕТ — ленивый импорт — но B-roll/HF/Remotion падают при вызове)
- **B1** Node.js ≥ 22.12 (render_scene.mjs/puppeteer, `npx remotion`, `npx hyperframes`).
- **B2** `npm install` (puppeteer+Chromium) + `npx puppeteer browsers install chrome-headless-shell` (системный snap-chromium НЕ работает).
- **B3** Развернуть рендер-проекты `panferov-broll` (Remotion) + `hyperframes-broll` (вне git: clone + npm install) → выставить `BROLL_PROJECT_DIR`/`HYPERFRAMES_PROJECT_DIR`/`HYPERFRAMES_BROWSER_PATH`.
- **B4** Claude Code CLI + подписка Max/Pro на сервере (оба движка зовут `subprocess.run(['claude','-p',...])`).

### B. ENV-секреты (в legacy хардкод-дефолты, в core пустые → фича отвалится молча)
- **E1/E2** `META_APP_ID` (`921654167162123`) + `META_APP_SECRET` — прописать в .env (G2: проверить/отозвать secret).
- **E3** `INSTAGRAM_WEBHOOK_VERIFY_TOKEN=panferov_ig_webhook_2026`.

### C. Раскомментировать регистрацию команд (код функций на месте; матчить по сигнатуре `CommandHandler("...")`, НЕ по номеру строки)
- `/update`, `/report`, `/launches`, `/brand` (≈ bot.py:21763/21764/21776/21780).
- Grep-проверка веток `action=='launches'/'brand'` в cmd_-роутере (~11390) + обработчик `cover_custom_input`.

### D. Tenant-конфиг
- **D1** Боевой panferov `tenant.json` из шаблона: `brands.allowed=[default,shoes]`, `launch_monitor/youtube_broll/billing=true`, `notion_db_id` Артёма, секреты `env:KEY`. (Уже подтверждён config_doctor strict.)
- **D2** Артёма сажать на panferov-тенант, НЕ на maksim (там launch_monitor=false → /launches заблокируется).

## Риски (проверить/мигрировать)
- **R3** `DEFAULT_BRAND` unset или `=default` (НЕ maksim — иначе карточки в чужой Notion).
- **R4** Переопределить `MAKSIM_MEDIA_DIR`/`MAKSIM_MUSIC_DIR`/`REMOTE_BOT_ROOT`/`*_PROJECT_DIR` под сервер Артёма (дефолты на инфру Максима).
- **R5** crosspost: `YOUTUBE_REDIRECT_URI` Артёма, `INSTAGRAM_TARGET_USERNAME` (в core хардкод `@livedrive_karting`).
- **R6** Per-tenant env с совпадающими именами (`ELEVENLABS_VOICE_ID`/`FISH_VOICE_ID`/`TELEGRAM_CHANNEL_ID`/`ADMIN_TELEGRAM_IDS`/`YOUTUBE_CLIENT_*`/`NOTION_*_DB`) — значения должны быть Артёма. Взять .env из content-bot-2 как базу.
- **R7** `CLAUDE_CODE_OAUTH_TOKEN`: если только API-ключ — НЕ задавать (иначе core уйдёт на подписочный путь).
- **R1** pending: вычистить эфемерные (`selfie_*`, `stats_draft`, `voice_parts`, **+`notion_edit_*`/`shotlist`** по G5), оставить `notion_page_id`+`card_data`+`script`+`card_brand`.

## OK (едет без вмешательства)
Ядро (start/help/notion/script/cards/ideas/calendar/stats/vk_auth/yt_auth/ig_auth/tgpost/image/video/heygen/selfie/crosspost/billing) — полный паритет, core ≥ legacy. Структуры данных (pending формат, projects именование, billing схема, Notion свойства+статусы, card_brand) — совместимы. Фичи Максима (карусели/банк идей/HF/Remotion) — загейчены panferov-тенантом, не всплывут. ffmpeg/Xvfb/Python-версия — не меняются.

---

## Исправленный чек-лист cutover (порядок)

**Фаза 0 — доразведка на сервере 65.21.154.237 (ОБЯЗАТЕЛЬНО, до любых действий):**
1. `pip freeze` — подтвердить elevenlabs/faster-whisper/httpx/requests-toolbelt/opencv + PTB `[job-queue]`/APScheduler + версию PTB.
2. `SELECT * FROM clients` в боевом billing Артёма — убедиться что это Артём (ДЫРА #1), найти точный путь файла.
3. Инвентаризация OAuth-токенов (YouTube/IG/VK refresh) + Telethon `.session` (G1) — где лежат, добавить в перенос.
4. Системные cron/systemd-таймеры Артёма (G4).
5. Жива ли Notion-схема как в диффе; боевой `notion_db_id` Артёма (G3).

**Фаза 1 — инфра B-roll:** Node 22 → npm install + chrome-headless-shell → рендер-проекты → Claude CLI → `pip install -r requirements.txt` + opencv.

**Фаза 2 — код:** раскомментировать 4 команды (по сигнатуре) + grep cmd_-роутер/cover_custom_input.

**Фаза 3 — конфиг/env:** боевой panferov tenant.json + `TENANT_STRICT=1`/`TENANT_ID_EXPECTED=panferov` + Meta-секреты + IG webhook token + пути R4 + crosspost R5 + DEFAULT_BRAND unset + per-tenant R6.

**Фаза 4 — данные (оба бота остановлены, БЕЗ активных сессий):** pending (вычищенный) + projects/ + assets/voices/ + ИМЕННО billing Артёма (после SELECT) + OAuth-токены + Telethon .session.

**Фаза 5 — смок на тест-токене:** doctor strict green → /cards читает карточки → /balance видит клиентов Артёма → launch_monitor/youtube_broll/selfie/crosspost(VK+YT+IG)/billing → /brand default↔shoes → leakage-route (/brand без maksim). Прогнать rollback_panferov.sh на тест-токене.

**Фаза 6 — боевой свитч:** по DEPLOY_RUNBOOK Phase 3 (unit-level stop old → start new boevой токен → smoke /start+callback+stateful → поэтапный disable старого).

HF-графика (hyperframes=true + резолв style_contract.panferov.json) — ПОСЛЕ успешного cutover, отдельным шагом.

---

## 🛡️ Защитный слой — принято из ChatGPT-ревью (16 июня, CONDITIONAL GO)

Внешнее ревью (`chatgpt_review_phase3_cutover_2026-06-16.md` в docs) сместило фокус с «unit переключился = ок» на **stateful rollback + живые данные** — главный риск разового cutover. Это ~0.5-1 сессия защитных работ ПЕРЕД боевым свитчем. Отфильтровано под масштаб (1 бот/1 сервер), все принятые пункты — реальная защита, не overkill.

**Принято (добавить в cutover до боевого свитча):**
- **C1 🔴 — snapshot state + restore-режим rollback.** Текущий `rollback_panferov.sh` откатывает только unit. Нужно: перед cutover — полный snapshot (`pending/billing.db/projects/assets/voices/OAuth-токены/Telethon .session/stats/calendar`) + `MANIFEST.sha256` + **репетиция restore на копии**. Rollback расширить: stop new → restore snapshot mutable-файлов → start old → verify. Иначе откат после частичного запуска нового = legacy-код на изменённом state.
- **C2 — drain перед stop**: нет активной генерации/ffmpeg/claude/node/puppeteer/upload/telethon + `lsof` на billing.db/pending.json + `PRAGMA integrity_check` sqlite. Не hard-cutover при активном job.
- **C4 — cutover-doctor**: расширить существующий `python -m tenant doctor` проверками: billing rows = Артём (не maksim), OAuth/Telethon файлы есть+парсятся, deps-версии (ffmpeg/node/npm/chrome/claude), команды /launches/update/report/brand зарегистрированы, prompt sha256, нет maksim/lifedrive маркеров в боевом env/tenant. Non-zero при любом blocker.
- **C5 — copy-on-cutover модель** (явно): old работает со своим state; new dry-run на КОПИИ; в freeze — stop old → финальный rsync → start new. НИКАКИХ symlink на pending/billing/projects между old/new.
- **I1** systemd обоих unit: `KillMode=control-group` + `TimeoutStopSec=30` + проверка `pgrep -af "/root/content-bot-2|/root/contentbot-core"` (по working dir, не bot.py).
- **I3** pending-мигратор по ALLOWLIST (не blacklist): перенести только `notion_page_id/card_data/script/card_brand/selected_brand`. Сохранить `pending.raw.json` + `pending.migrated.json` + diff.
- **I4** billing semantic check (не только SELECT clients): `PRAGMA integrity_check` + `.schema` + COUNT clients/operations + SUM + app-level `/balance` на копии + idempotency списания на копии.
- **I5** commands registration в doctor (автотест `expected_commands <= registered`), не ручной grep.
- **I6** media public URL check (`curl -I` боевого медиа-домена + nginx root).
- **Post-start watch 15 мин** (не только /start): T+1 логи чистые, T+3 /start+/brand+/cards+/balance, T+5 реальная карточка + безопасный preview (без paid/publish), T+10 OAuth readiness, T+15 нет traceback/polling-conflict/tenant-fallback.
- **N1-N3** (дёшево): `cutover_status.sh` (active unit/tenant/commit/strict/paths/last errors), `DEPLOYED_COMMIT` файл, `grep -RInE "maksim|livedrive|yumsunov|life drive"` боевого env/tenant/prompts.

**ОТФИЛЬТРОВАНО (оспорено, НЕ берём как предложено):**
- **C3 `DRY_RUN_EXTERNALS=1` как флаг в коде** — нет. Писать в бот режим отключения Notion-write/crosspost/paid — разработка кода ради разового cutover (против минимализма). Замена: смок на тест-токене с **тест-конфигом без боевых OAuth-кредов** (соцсети не подключены) + **readiness-check токенов отдельным скриптом** (валиден ли YouTube/IG/VK/Telethon, без публикации). Тот же эффект, без нового кода в боте.

**Обновлённый порядок Phase 3:** Фаза 0 (доразведка) → **защитные работы (snapshot+restore-репетиция, drain-чек, cutover-doctor, pending-мигратор, systemd KillMode, readiness-check)** → Фаза 1-3 (инфра/код/конфиг) → смок тест-токен → Фаза 6 (боевой свитч + post-start watch). Финальный GO-чеклист — раздел 5 ревью-файла.

---

## ✅ Фаза 0 — РЕЗУЛЬТАТЫ доразведки сервера 65.21.154.237 (16 июня)

Инструмент: `tools/cutover_doctor.py` (TDD, 13 тестов GREEN, коммит 300f60d) + ручной ssh read-only. Несколько блокеров СНЯТЫ фактом:

- **✅ DEPS — блокер СНЯТ (ДЫРА #3 закрыта).** В venv Артёма (`/root/content-bot/venv`) уже стоят ВСЕ требуемые: APScheduler 3.11.2 (job-queue→launch_monitor cron не упадёт), elevenlabs 2.42.0 (>2.40), faster-whisper 1.2.1, httpx 0.28.1, opencv-python-headless 4.13 (+contrib+python), PTB 21.6, requests-toolbelt 1.0. `pip install -r requirements.txt` для выравнивания пинов, но критичное есть. Node-стек (B-roll) — отдельно (Фаза 1), не проверялся.
- **🔴 BILLING — уточнение ДЫРЫ #1.** На сервере Артёма `/root/content-bot/billing/billing.db` (57KB, `BILLING_ENABLED=1`) содержит ЕДИНСТВЕННОГО клиента = **Максим** (`telegram_id=111, maksim_new, bot_instance=lifedrive`, 8 balance_ops). Это тот же тестовый снапшот, что в core. **Реальных платящих клиентов Артёма в billing НЕТ** — это остаток разработки billing. **РЕШЕНО (Артём 16 июня):** billing пока по сути выключен, но это БУДУЩАЯ задача (сделать+продумать billing). На cutover: billing.db НЕ переносить, на core почистить тестового Максима (начать с пустой базы), billing-инфру (пакет + BILLING_ENABLED) оставить — понадобится. cutover_doctor правильно флагует lifedrive как чужого → перед cutover очистить billing/billing.db от Максима. Отдельная задача «продумать и доделать billing Артёма» — в backlog (не блокер cutover).
- **✅ G1 OAuth/Telethon — закрыт (что переносить).** В `/root/content-bot/`: `instagram_token.json` (Apr 30), `vk_token.json` (Apr 16), `youtube_token.json` (Apr 24), `telethon_session.session` (49KB, свежий после фикса 15 июня). Переносить эти 4 файла. ⚠️ IG-токен ~47 дней (живёт ~60) — reauth скоро.
- **✅ G4 CRON — закрыт.** Системных cron/timers Артёма нет (только `certbot.timer`). launch_monitor не на системном cron (job_queue/пауза). Переносить нечего.
- **✅ G5 активные сессии — на момент проверки чисто.** pending = 2 юзера (@Trader state=None, Артём state=done) — НЕТ активного редактирования. Cutover в такой момент безопасен (перепроверить непосредственно перед Фазой 4).
- **📏 Размер state**: projects 952MB + assets 118MB ≈ 1GB (rsync-окно минуты), pending 4KB. Учесть в downtime Фазы 4.

**Осталось из Фазы 0 (требует развёрнутого core / стабильного ssh):** запуск `cutover_doctor` уже НА СЕРВЕРЕ в окружении core (Фаза 5 gate, после развёртывания core в Фазе 1) + readiness-check валидности OAuth-токенов (не истёк ли IG).
