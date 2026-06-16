# CTO Review — Phase 3 cutover `panferov` → `contentbot-core`

**Дата ревью:** 2026-06-16  
**Объект:** план боевой пересадки работающего `panferov`-бота на `contentbot-core`  
**Роль:** внешний CTO / adversarial reviewer  
**Вердикт:** **CONDITIONAL GO** — план близок к боевому, но запускать cutover можно только после закрытия Critical-блокеров ниже.

---

## 0. Executive verdict

План стал заметно зрелее: strict tenant-mode, `TENANT_ID_EXPECTED`, rollback-скрипт, учёт billing/OAuth/Telethon/session-файлов, инфраструктурная доразведка и canary-модель — это правильное направление.

Но на боевой cutover я бы дал **GO только после 5 обязательных добавлений**:

1. **Единый pre-cutover snapshot всего состояния** с manifest/checksum и пробным restore.
2. **Freeze-window + drain active jobs** перед копированием `pending/projects/db/session` — не просто “остановили старый”, а убедились, что нет активной генерации/рендера/загрузки/SQLite-writer.
3. **Dry-run нового бота на копии живого state**, но с тестовым TG-токеном и отключёнными внешними публикациями.
4. **Post-start watch window 10–15 минут**: новый бот должен не просто стартовать, а пережить lazy-import путей `/stats`, `/brand`, `/launches`, media upload, OAuth check, billing read.
5. **Rollback должен возвращать не только unit, но и данные/токены/конфиг к snapshot**, иначе rollback после частичного запуска нового может поднять старый код на уже изменённом state.

Без этих пунктов риск не в том, что бот “не стартанёт”. Риск в том, что он стартанёт, пройдёт `/start`, а потом сломается на первом реальном stateful-действии или испортит/перемешает данные.

---

## 1. 🔴 Critical findings

### C1. Rollback сейчас откатывает процесс, но не откатывает состояние

**Проблема:** скрипт останавливает `contentbot-core` и запускает `content-bot`, но не гарантирует, что старый бот увидит прежние файлы: `pending.json`, `billing.db`, OAuth-token files, Telethon `.session`, `projects/`, stats/history/calendar, media cache.

Если новый бот успел:
- изменить `pending.json`;
- записать billing operation;
- обновить OAuth refresh-token;
- создать/переместить project files;
- изменить stats/calendar;
- начать upload/crosspost;
- обновить session-файл;

то rollback на старый unit может поднять legacy-код на **уже модифицированном state**. Это самый неприятный класс ошибок: unit активен, но данные уже не те.

**Fix до cutover:**

Перед Phase 4 сделать полный snapshot state в read-only backup directory:

```bash
SNAP=/root/cutover_snapshots/panferov_$(date +%Y%m%d_%H%M%S)
mkdir -p "$SNAP"

# examples, уточнить реальные пути по inventory
rsync -a --numeric-ids /root/content-bot-2/ "$SNAP/content-bot-2/"
rsync -a --numeric-ids /root/contentbot-core/ "$SNAP/contentbot-core_pre/"
sqlite3 /path/to/billing.db ".backup '$SNAP/billing.db'"
cp -a /path/to/*.json "$SNAP/" 2>/dev/null || true
cp -a /path/to/*.session "$SNAP/" 2>/dev/null || true

find "$SNAP" -type f -print0 | sort -z | xargs -0 sha256sum > "$SNAP/MANIFEST.sha256"
```

Rollback должен иметь режим:

1. stop new;
2. restore state snapshot или хотя бы restore тех файлов, которые могли быть изменены;
3. start old;
4. verify old health.

Минимально: **сделать explicit список mutable files** и решить, какие из них restore-ятся при rollback, а какие остаются.

---

### C2. Не хватает drain-проверки активных тяжёлых задач перед stop/copy

**Проблема:** в плане есть “оба бота остановлены”, но нет строгого drain-чека: нет ли активной генерации, ffmpeg, Claude Code, upload, Telethon uploader, locked SQLite, временных project dirs, pending-флоу в процессе.

Риск: вы остановите Telegram handler, но дочерний процесс/поток/внешний helper может продолжать писать в files/db. История с `telethon-uploader` и SQLite-lock уже доказала, что это не теоретика.

**Fix до cutover:**

Добавить pre-stop/drain checklist:

```bash
# нет активных тяжёлых процессов старого бота
pgrep -af "ffmpeg|remotion|hyperframes|claude|node|puppeteer|chrome|yt-dlp|telethon" || true

# нет открытых дескрипторов на критичные файлы
lsof /path/to/billing.db /path/to/pending.json 2>/dev/null || true
lsof +D /path/to/projects 2>/dev/null | head -50 || true

# SQLite consistency
sqlite3 /path/to/billing.db "PRAGMA integrity_check;"
sqlite3 /path/to/telethon.session "PRAGMA integrity_check;" 2>/dev/null || true
```

Если есть активный job — не делать hard cutover. Либо дождаться, либо явно отменить и зафиксировать, что pending очищается.

---

### C3. Smoke на тестовом токене может дать ложную уверенность из-за отключённых внешних контуров

**Проблема:** тестовый Telegram-токен полезен, но он не равен боевому окружению:
- другие OAuth redirect/callback assumptions;
- другие chat_id/channel_id;
- media URLs могут быть привязаны к боевому домену;
- Notion DB и stats могут быть боевыми;
- Telegram bot token влияет на file API/download paths;
- crosspost может случайно пойти в реальные соцсети, если токены боевые.

**Fix до cutover:**

Ввести режим **DRY_RUN_EXTERNALS=1** для smoke:

- Notion write → disabled или sandbox DB;
- crosspost publish → disabled, только token/status check;
- billing mutation → disabled или copy db;
- HeyGen/ElevenLabs paid calls → disabled;
- upload to TG/VK/YT/IG → disabled;
- Claude/render можно smoke только на маленьком фикстурном сценарии.

И отдельно сделать **external readiness check** без публикации:

```text
- Instagram token valid?
- VK token valid?
- YouTube refresh token valid?
- Notion DB reachable and schema matches?
- Telethon session authorized?
- Media nginx URL reachable from public internet?
```

---

### C4. Нужен `contentbot-core --doctor` как отдельный предбоевой gate, а не только startup log

**Проблема:** strict startup хорошо ловит tenant/config, но часть рисков cutover не покрыта:
- наличие `billing.db` именно Артёма;
- schema compatibility billing/pending/stats;
- OAuth/token files присутствуют;
- Telethon session opens;
- Node/Chrome/ffmpeg/Claude CLI versions;
- prompt hashes;
- media dirs counts;
- commands registered;
- external env vars.

**Fix до cutover:**

Сделать отдельную команду:

```bash
python -m tools.cutover_doctor --tenant panferov --strict --state-root /root/contentbot-core
```

Она должна возвращать non-zero при любом blocker. Минимальный состав:

```text
[config] tenant_id == panferov
[config] allowed_brands == default, shoes
[config] features expected true/false
[env] required env present, no maksim/lifedrive markers
[secrets] META_APP_ID/SECRET set, no hardcoded fallback
[files] prompt files exist + sha256
[state] billing.db client rows match panferov / no maksim
[state] pending schema parse ok
[state] projects count > expected threshold
[state] stats_history/pub_calendar parse ok
[oauth] token files exist and parse
[telethon] session file exists and sqlite integrity ok
[deps] ffmpeg/node/npm/chrome/claude available
[commands] /launches /update /report /brand registered
```

---

### C5. Cutover checklist должен явно запрещать “shared mutable state” между old и new

**Проблема:** если новый core читает старые state-файлы по symlink/shared path, rollback становится хрупким. Если копирует — появляется проблема divergence. Сейчас план говорит “копия state”, но нужно зафиксировать модель.

**Рекомендация:** для Phase 3 выбрать **copy-on-cutover**, не shared state.

Правильная схема:

```text
old legacy dir: /root/content-bot-2
new core dir:  /root/contentbot-core

До cutover:
  old работает со своим state.
  new dry-run работает на КОПИИ state, не пишет в old.

В cutover freeze:
  stop old
  final rsync old state → new state
  start new

Rollback:
  stop new
  restore old state from pre-cutover snapshot или принять explicit список изменений
  start old
```

Shared symlink на `pending.json`, `billing.db`, `projects/` между old/new — **не рекомендую**.

---

## 2. 🟡 Important findings

### I1. `pgrep -f bot.py` заменён правильно, но нужно проверить child process group

Вы уже отказались от общего `pgrep`, потому что на сервере есть второй Python-бот Nox. Это правильно. Но `systemctl stop` не всегда убивает дочерние процессы, если unit настроен нестрого.

**Добавить в systemd для обоих unit:**

```ini
KillMode=control-group
TimeoutStopSec=30
Restart=on-failure
```

И в check:

```bash
systemctl show content-bot -p MainPID,ControlPID,SubState,Result
systemctl status content-bot --no-pager
```

Плюс после stop проверить не только `MainPID=0`, но и отсутствие процессов по working directory:

```bash
pgrep -af "/root/content-bot-2|/root/contentbot-core" || true
```

Это уже безопаснее, чем `pgrep -f bot.py`.

---

### I2. OAuth/Telethon: копировать можно, но надо иметь manual re-auth path

**Оценка:** копировать OAuth-token files и Telethon `.session` нормально, если:
- тот же сервер/пользователь/права доступа;
- не меняется app_id/app_secret;
- файлы не повреждены;
- новый код ожидает тот же формат.

Но нужен fallback-plan: если session/token invalid — не пытаться чинить в боевом cutover 2 часа руками.

**До cutover:**
- сделать read-only check токенов;
- сохранить точные пути;
- проверить owner/permissions после копирования;
- иметь инструкцию “как переавторизовать YouTube/VK/IG/Telethon”;
- решить, что будет с публикациями, если один OAuth отвалился: бот должен стартовать, но feature должна показать понятную ошибку.

**Важно:** если Meta-секреты были в legacy plaintext/git, считать их потенциально скомпрометированными. Не блокер cutover, но после стабилизации — rotate.

---

### I3. Pending cleanup нужно делать по allowlist, а не по blacklist

В плане: “вычистить эфемерные, оставить `notion_page_id` + `card_data` + `script` + `card_brand`”. Это правильное направление, но безопаснее формализовать как мигратор:

```python
ALLOWED_PENDING_KEYS = {
    "notion_page_id",
    "card_data",
    "script",
    "card_brand",
    "selected_brand",
    # только явно подтверждённые
}
```

Не “удалить известный мусор”, а “перенести только известное безопасное”.  
Перед миграцией сохранить `pending.raw.json`, после — `pending.migrated.json`, плюс diff summary.

---

### I4. Billing transfer: нужен semantic check, не только `SELECT clients`

`SELECT * FROM clients` поймает “это база Максима”, но не гарантирует совместимость.

Минимальный gate:

```sql
PRAGMA integrity_check;
.schema
SELECT COUNT(*) FROM clients;
SELECT id, name, telegram_id, balance_rub FROM clients;
SELECT COUNT(*) FROM operations;
SELECT SUM(amount_rub) FROM operations;
```

И приложение-level check:
- `/balance` на тест-токене;
- создание test debit/credit на копии db;
- idempotency списания на копии;
- rollback db после test.

---

### I5. Проверить commands registration автоматически

Риск “4 команды раскомментированы, но не зарегистрировались из-за feature flag / import error / handler order” лучше ловить не ручным grep.

Добавить тест/doctor:

```python
expected_commands = {"launches", "update", "report", "brand"}
registered = extract_registered_command_handlers(application)
assert expected_commands <= registered
```

И Telegram smoke:
- `/brand` показывает default/shoes, не maksim;
- `/update` не говорит “закомментировано/недоступно”;
- `/launches` не запускает внешние paid/download actions без подтверждения;
- `/report` не падает на stats db.

---

### I6. Media/public URL check до cutover

Кросспостинг и IG/YouTube часто ломаются не на токене, а на публичной доступности URL. Добавить:

```bash
curl -I https://<media-domain>/<known-test-file>
curl -I http://localhost/<local-media-path> # если nginx proxy
```

И проверить, что new core публикует media в тот же nginx root, что и старый, либо env явно переопределён.

---

### I7. Maintenance message — не обязательно, но лучше для владельца

Для владельца-бота можно жить с окном 30–90 секунд. Но я бы всё равно отправил себе в бот сообщение:

```text
⚙️ Техобслуживание 5–10 минут. Если кнопка не отвечает — просто нажми ещё раз позже.
```

Это дешевле, чем потом разбирать “бот завис”.

---

## 3. 🟢 Nice-to-have

### N1. `cutover status` command

Сделать простой скрипт:

```bash
./cutover_status.sh
```

Выводит:
- active unit;
- tenant id;
- git commit;
- strict mode;
- expected tenant;
- bot username;
- DB path;
- pending path;
- media root;
- last 50 errors.

Очень помогает в панике.

### N2. Git tag / release marker перед cutover

Перед стартом нового core:

```bash
git rev-parse HEAD > /root/contentbot-core/DEPLOYED_COMMIT
```

В `/start` или admin `/debug` показывать commit/tenant/features.

### N3. Автоматическая проверка “нет maksim/lifedrive markers”

Перед cutover:

```bash
grep -RInE "maksim|livedrive|yumsunov|life drive" \
  tenant.json .env prompts/ config/ --exclude='*.example*'
```

В core-коде могут быть допустимые примеры, но в боевом env/tenant/prompts их быть не должно.

---

## 4. Ответы на конкретные вопросы

### Q1. Чего не хватает в чек-листе cutover?

Главные недостающие пункты:

1. **State snapshot + manifest + restore rehearsal.**
2. **Drain active jobs/processes/locks.**
3. **Dry-run на копии живого state с DRY_RUN_EXTERNALS.**
4. **Cutover doctor как отдельная команда.**
5. **Post-start watch 10–15 минут, а не только `/start`.**
6. **Rollback state, не только rollback unit.**
7. **Проверка media public URL/nginx root.**
8. **Автоматический check отсутствия maksim/lifedrive markers в боевом конфиге.**

---

### Q2. Перенос данных: порядок безопасен? Нужен ли snapshot?

Текущий порядок в целом правильный, но **snapshot обязателен**.

Без snapshot вы не сможете отличить:
- “новый бот изменил state”;
- “старый state уже был таким”;
- “копирование прошло частично”;
- “rollback поднял старый код на новом state”.

Рекомендованный порядок:

```text
1. Announce maintenance.
2. Stop old.
3. Verify old stopped + no child writers + no locks.
4. Snapshot old state.
5. Validate snapshot manifest.
6. Copy state old → new.
7. Run new doctor on copied state.
8. Start new on battle token.
9. Watch.
10. If rollback: stop new → restore snapshot if needed → start old.
```

Если downtime нужно сократить: предварительный rsync можно сделать до stop, но финальный rsync всё равно после stop.

---

### Q3. Фаза 0 доразведка достаточна? Что ещё проверить?

Добавить:

- `systemctl cat content-bot` и `systemctl cat contentbot-core` — env files, working dir, user, KillMode.
- `env`/`.env` diff against expected panferov env schema.
- `du -sh` state dirs, чтобы понимать время копирования.
- `find` всех sqlite/db/json/session/token файлов.
- `lsof` по state dirs.
- `sqlite3 PRAGMA integrity_check` для всех sqlite.
- `python -m compileall` или быстрый import smoke core.
- `ffmpeg -version`, `node -v`, `npm -v`, `claude --version`.
- `python -c "import cv2, apscheduler, telegram"` — проверка проблемных deps.
- public media URL check.
- OAuth dry checks без публикации.
- Notion schema check на живой DB.

---

### Q4. Rollback-скрипт покрывает реальные провалы?

Он покрывает только один класс: “новый unit надо остановить, старый поднять”. Не покрывает:

- новый стартовал, но падает через минуту;
- новый изменил state;
- новый держит дочерний процесс;
- token polling conflict не ушёл сразу;
- старый unit стартанул, но не отвечает;
- старый стартует на повреждённом/новом state.

Добавить:

```bash
# wait active stable
sleep 15
journalctl -u contentbot-core -n 100 --no-pager | grep -Ei "critical|traceback|tenant|polling|conflict"

# after rollback old smoke marker
systemctl is-active content-bot
journalctl -u content-bot -n 100 --no-pager | grep -Ei "traceback|conflict|unauthorized"
```

И отдельный restore-state mode.

---

### Q5. Downtime / активные пользователи

Для владельца-бота окно допустимо. Но я бы сделал maintenance-сообщение и не проводил cutover во время активной генерации/загрузки.

Callback, отправленный во время stop/start, может потеряться. Это ок, если пользователь знает, что идёт техобслуживание. Главное — не делать cutover между “оплатил/подтвердил/запустил рендер” и “получил результат”.

---

### Q6. OAuth-токены и Telethon-сессия: копировать или переавторизоваться?

**Сначала копировать**, потому что это быстрее и менее рискованно для короткого cutover, если сервер тот же. Но иметь re-auth runbook.

Порядок:
1. copy files with permissions;
2. read-only token check;
3. if failed — do not block whole bot, disable feature or show connected=false;
4. after стабилизации — rotate/reauth compromised Meta secrets.

Telethon `.session` особенно проверить через read-only “authorized?” smoke до боевого старта, потому что SQLite/session lock уже был проблемой.

---

### Q7. Что из “совместимо” проверить на живых данных?

Обязательно:

1. **Notion schema**: названия properties/status/options, create/update test на sandbox/card copy.
2. **Billing schema + balances**: реальные clients/operations.
3. **Pending format**: parse + migrate allowlist.
4. **Projects dir**: открыть 2–3 реальные карточки, найти assets/scripts/media.
5. **Prompt paths/hash**: default/shoes.
6. **Stats db/json/calendar**: `/stats`, `/update`, `/report`.
7. **OAuth tokens**: status check.
8. **Feature gates**: `panferov` не видит maksim-only UI, `allowed_brands` только default/shoes.
9. **Crosspost env**: нет `maksim-bot`, `livedrive`, `@yumsunov86` в боевом panferov-контуре.

---

## 5. Suggested revised GO checklist

### GO prerequisites

```text
[ ] cutover_doctor --strict green
[ ] full state snapshot created + manifest
[ ] restore procedure tested on non-prod copy
[ ] old unit drain: no child writers, no locks
[ ] billing db verified as panferov
[ ] OAuth/token/session files inventoried and copied
[ ] media public URL check green
[ ] tenant.json strict + expected_id=panferov
[ ] grep no maksim/lifedrive markers in live env/config/prompts
[ ] test-token smoke on copied live state green
[ ] DRY_RUN_EXTERNALS enabled for test-token smoke
[ ] rollback script + state restore ready
[ ] maintenance window confirmed
```

### Post-start watch

```text
T+0: service active
T+1m: logs clean
T+3m: /start, /brand, /cards, /balance
T+5m: open real card, generate harmless preview, no paid/publish
T+10m: OAuth readiness checks
T+15m: no traceback / no polling conflict / no tenant fallback
```

---

## 6. Final verdict

**Текущий план: NO-GO для немедленного боевого cutover.**

Не потому что архитектура плохая, а потому что cutover сейчас слишком сильно завязан на “unit переключился = всё хорошо”. Для этого проекта главный риск — **stateful rollback и живые данные**, а не старт Python-процесса.

**После добавления snapshot/restore, drain-check, cutover_doctor, dry-run на копии живого state и post-start watch — GO.**

С учётом масштаба это не overengineering. Это 0.5–1 сессия защитных работ, которые резко снижают риск сломанного боевого бота и грязного rollback.
