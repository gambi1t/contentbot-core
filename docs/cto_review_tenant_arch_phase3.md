# CTO Review: tenant-архитектура as-built + Phase 3 cutover

**Контекст:** `contentbot-core`, Phase 2a tenant shell, план Phase 3 — боевая пересадка `panferov` на core.  
**Фокус ревью:** прод-риск, изоляция тенантов, cutover polling-бота, достаточность `config_doctor`, YAGNI для масштаба 2 тенанта / 1 сервер.

---

## 0. Вердикт

**Phase 3 можно делать, но не в текущем виде.** Сам подход правильный: один процесс = один tenant, `panferov` как canary, `maksim` как production, no-op tenant-layer без `tenant.json` для текущих продов. Но перед боевой пересадкой надо закрыть несколько маленьких, но принципиальных дыр.

Главный риск сейчас: tenant-layer уже выглядит как конфигурационная система, но в критических местах ведёт себя как мягкий best-effort слой. Для canary это терпимо, для боевого cutover — нет.

**Минимальный go/no-go перед Phase 3:**

1. `config_doctor` в Phase 3 должен быть **fatal**, если `tenant.json` существует и невалиден.
2. Все `env:KEY` в `brand_overrides` для активного tenant/brand должны быть проверены до старта.
3. Нужно добавить `TENANT_ID_EXPECTED=panferov` или аналогичный startup-guard.
4. Feature-gate должен быть не только callback-prefix guard, а хотя бы дополнен command/background guard для отключённых фич.
5. Cutover должен проверять не просто `pgrep -f bot.py`, а конкретный unit/token-lock/process group.

После этого Phase 3 можно проводить как canary с быстрым rollback.

---

# 🔴 Critical

## C1. `config_doctor` сейчас warning-only: для Phase 3 это недопустимо

### Почему это критично

Сейчас при проблемах конфига бот просто пишет:

```python
logger.warning(f"[tenant] config doctor: {_tenant_problems}")
```

и продолжает старт. Это нормально для переходного состояния **без `tenant.json`**, но опасно для Phase 3, где `tenant.json` уже становится источником правды.

Риск: вы можете стартануть `panferov` на core с частично битым конфигом, отсутствующим `features`, неправильным `tenant_id`, неизвестными флагами или незарезолвленными override-полями — и бот будет работать на fallback-значениях из `BRANDS`/`.env`/старого кода.

Для канареечного cutover это хуже, чем явный crash. Явный crash останавливает rollout. Тихий fallback может выдать неправильный голос, неправильный avatar_id, не те кнопки, не тот Notion или старые брендовые следы.

### Что исправить

Ввести режим строгого старта:

```python
TENANT_STRICT=1
TENANT_ID_EXPECTED=panferov
TENANT_CONFIG=/root/contentbot-core/tenant.json
```

Логика:

- если `TENANT_STRICT=0` и `tenant.json` отсутствует → fallback `default`, no-op;
- если `TENANT_STRICT=1` и `tenant.json` отсутствует → **fatal startup error**;
- если `tenant.json` есть, но `config_doctor` вернул проблемы → **fatal startup error**;
- если `tenant_id != TENANT_ID_EXPECTED` → **fatal startup error**.

### Однострочный фикс

`bot.py startup`: заменить warning-only на fail-fast при `TENANT_STRICT=1` или при наличии реального `tenant.json`.

---

## C2. `env:KEY` без значения silently falls back to code — это маскирует misconfig

### Почему это критично

В `apply_brand_overrides` сейчас:

```python
rv = _resolve_env(v)
if rv is not None:
    merged[k] = rv
```

Если `tenant.json` говорит:

```json
"heygen_avatar_id": "env:HEYGEN_AVATAR_ID"
```

а переменной нет, поле просто не затирается. В итоге код тихо берёт значение из `BRANDS`. Для Phase 2a это было удобно как мост, но для Phase 3 это опасно.

Особенно плохо, если в `BRANDS` остались значения от другого бренда или старого форка. Такой баг не упадёт на старте, а проявится только в видео: не тот avatar, не тот voice, не тот prompt-файл, не тот Notion routing.

### Что исправить

Разделить поведение:

- **transitional mode:** env missing → skip override;
- **strict tenant mode:** env missing → config doctor error.

Добавить в `config_doctor` обход `brand_overrides`:

```python
if isinstance(v, str) and v.startswith("env:"):
    key = v[4:]
    if not os.getenv(key):
        problems.append(f"missing env var for brand_overrides.{brand}.{field}: {key}")
```

Важно: проверять только активные brand overrides текущего tenant, чтобы не требовать env для всех example-конфигов.

### Однострочный фикс

`tenant.py/config_doctor`: в strict mode валидировать все `env:*` ссылки и падать до старта, если переменная отсутствует.

---

## C3. Callback-level feature gate не является полноценной изоляцией фич

### Почему это критично

Сейчас gate стоит в начале `handle_callback` и проверяет только префиксы:

```python
_CALLBACK_FEATURE_MAP = {
    "carousel_": "carousel",
    "idea_pipeline:": "idea_bank",
    "launch_skip:": "launch_monitor",
    ...
}
```

Это лучше, чем просто скрыть кнопки. Но это не полноценная изоляция:

1. Команды (`/launches`, `/stats`, `/report`, `/carousel`, `/idea`, `/image`) могут обходить callback gate.
2. Message handlers могут принимать состояние отключённой фичи, если pending/state остался после миграции.
3. Background jobs/cron могут запускать отключённую фичу вообще без callback.
4. Prefix-map легко неполный: например, `carousel_` не покрывает callback вида `carousel:` или `card_to_carousel:` — зависит от реальных callback names.
5. Если фича выключена, но старое сообщение с inline-кнопкой осталось в Telegram, callback gate должен покрывать **все** legacy callback patterns.

### Что исправить до Phase 3

Не нужен enterprise registry, но нужен минимальный `requires_feature` слой:

```python
def require_feature(name: str) -> bool:
    if _tenant.feature_blocked(_ACTIVE_TENANT, name):
        return False
    return True
```

И применить в трёх местах:

1. Callback prefix gate — оставить.
2. Команды отключаемых фич — проверить явно в начале handler-а.
3. Background jobs — не регистрировать, если фича выключена.

Для Phase 3 минимум: пройти grep по handler registration и callback prefixes для всех `_KNOWN_FEATURES`.

### Однострочный фикс

`bot.py`: дополнить callback-gate feature guard-ом на command handlers/background jobs и покрыть legacy callback prefixes (`card_to_carousel:`, `carousel_tpl:`, etc.).

---

## C4. Phase 3 cutover проверяет “нет bot.py”, но не гарантирует “нет процесса с этим Telegram token”

### Почему это критично

Polling-бот с одним Telegram token должен быть строго один. В runbook есть:

```bash
systemctl stop content-bot && pgrep -f bot.py → пусто
```

Это слабая проверка:

- `pgrep -f bot.py` может поймать не тот процесс или не поймать wrapper/process с другим argv;
- старый процесс может быть в состоянии shutdown, но ещё держать polling/session;
- новый и старый unit могут иметь разные working dirs, но один token;
- crash-loop другого сервиса может мешать диагностике;
- если старый unit имеет restart policy, он может подняться снова.

### Что исправить

Перед start нового:

1. Старый unit: `systemctl stop content-bot`.
2. Проверить `systemctl is-active content-bot` → inactive.
3. Проверить `systemctl show content-bot -p MainPID` → `MainPID=0`.
4. Проверить lockfile по hash токена, а не просто процесс.
5. Новый unit должен иметь `ExecStartPre`, который создаёт exclusive lock:

```bash
flock -n /run/contentbot-panferov-token.lock -c '...start bot...'
```

или Python-level lock до запуска polling.

6. В старом unit временно поставить `Restart=no` или `systemctl disable --now content-bot` только на время cutover. После успешного cutover старый оставить disabled, но rollback script должен уметь включить обратно.

### Однострочный фикс

`Phase 3 runbook`: заменить `pgrep -f bot.py` на unit-level + token-lock verification; добавить `ExecStartPre`/flock и rollback script.

---

## C5. Нет startup-guard от случайного запуска не того tenant-конфига

### Почему это критично

Сейчас `TENANT_CONFIG` может указывать на любой файл, а если не указывает — берётся `tenant.json` рядом с кодом. Для Phase 3 это потенциальная мина: можно случайно стартовать `panferov`-токен с `tenant_id=default` или с конфигом `maksim`.

Это особенно опасно в модели “один код, разные серверы/каталоги”. Ошибка пути или забытый env — и бот запускается с fallback.

### Что исправить

Добавить обязательную проверку:

```python
expected = os.getenv("TENANT_ID_EXPECTED")
if expected and tenant.get("tenant_id") != expected:
    raise RuntimeError(f"tenant_id mismatch: expected {expected}, got {tenant.get('tenant_id')}")
```

Для Phase 3:

```env
TENANT_STRICT=1
TENANT_ID_EXPECTED=panferov
TENANT_CONFIG=/root/contentbot-core/tenant.json
```

### Однострочный фикс

`tenant.py/bot.py startup`: добавить `TENANT_ID_EXPECTED` и падать при несовпадении.

---

# 🟡 Important

## I1. Fail-open gate vs fail-closed flags — корректно только как временный bridge

### Оценка

Асимметрия в целом разумная для Phase 2a:

- `feature_enabled` default false — не показываем новые кнопки новому tenant без явного включения;
- `feature_blocked` default allow — не ломаем старые проды, где `tenant.json` ещё не существует.

Но это должно быть явно ограничено переходным режимом.

### Риск

Если у tenant уже есть `tenant.json`, но в нём забыли указать фичу, `feature_blocked` не заблокирует прямой callback. То есть “кнопка не показана” ≠ “фича недоступна”. Для тарифов и изоляции это дыра.

### Рекомендация

Добавить mode-aware semantics:

```python
def feature_blocked(tenant, name):
    if tenant.get("tenant_id") == "default" and not tenant_config_exists:
        return False  # transitional no-config mode
    feats = tenant.get("features") or {}
    return feats.get(name) is not True  # strict: only explicit true allows optional feature
```

Но делать это сразу для текущих продов нельзя. Поэтому:

- до Phase 3: оставить как есть для no-config;
- для Phase 3 strict mode: optional фича разрешена только при `true`.

---

## I2. `load_tenant` не обрабатывает JSON parse errors с понятной диагностикой

Если `tenant.json` битый, `json.load` бросит exception. Это лучше, чем тихий fallback, но будет некрасивый crash без friendly context.

### Рекомендация

Обернуть в `TenantConfigError`:

```python
try:
    return json.load(f)
except json.JSONDecodeError as e:
    raise TenantConfigError(f"invalid tenant config {p}: {e}")
```

Для Phase 3 это поможет быстрее откатиться и не гадать, почему unit падает.

---

## I3. `_brand_with_overrides` как единственная точка merge — хорошо, но прямые `BRANDS[...]` чтения надо классифицировать до Phase 3

### Оценка

Если 4 runtime-чтения provider ID точно идут через `_get_active_brand()`, это закрывает самый дорогой класс ошибок: wrong avatar / wrong voice.

Но прямые `BRANDS[...]` чтения в UI-пикерах и описаниях всё равно могут создать проблемы:

- показать пользователю не те бренды;
- показать старые имена/описания;
- дать выбрать бренд, которого нет в tenant;
- leakнуть `maksim`-следы в `panferov` UI.

Это может не сломать render, но сломает доверие при canary.

### Рекомендация

До Phase 3 не нужно выносить весь `BRANDS`. Но нужно сделать `grep`-таблицу:

| Direct read | Runtime critical? | Phase 3 action |
|---|---:|---|
| provider IDs | yes | must use override |
| brand picker list | medium | filter by tenant.allowed_brands |
| UI labels | medium | acceptable only if correct for panferov |
| debug/log only | no | defer |

Минимальный конфиг:

```json
"brands": {
  "active": "default",
  "allowed": ["default", "shoes"]
}
```

И в `/brand` picker показывать только `allowed`.

---

## I4. `config_doctor` перед Phase 3 должен проверять не только структуру

Текущий doctor валидирует только:

- наличие `tenant_id`, `features`;
- известность флагов;
- bool-типы фич.

Для Phase 2a это ок. Для Phase 3 мало.

### Что добавить до cutover

Минимальный Phase 3 doctor:

1. `tenant_id == TENANT_ID_EXPECTED`.
2. `features` содержит все known features или хотя бы все required для данного tenant.
3. Все `env:*` из активных overrides существуют.
4. `brand_overrides` ссылается на существующий `brand_name` в `BRANDS`.
5. Override-ключи входят в allowlist (`heygen_avatar_id`, `eleven_voice_id`, `script_prompt_file`, `cover_prompt_file`, etc.).
6. Prompt-файлы существуют и не пустые.
7. Media directories из state inventory существуют.
8. `billing.db`, `stats_history.json`, `pub_calendar.json` либо существуют, либо явно помечены как optional/empty.
9. Telegram token проходит `getMe` smoke отдельно в runbook.

### Что можно отложить в 2b

- live проверку HeyGen/ElevenLabs генерации;
- Notion write test;
- глубокую проверку всех медиа-ассетов;
- leakage-тесты по всем выходным видео.

---

## I5. Cutover runbook не фиксирует состояние update queue

Polling упрощает cutover, но есть нюанс: старый бот мог получить update, но не успеть обработать; новый после старта может получить следующий offset. Обычно это не критично, но во время migration может потеряться callback или команда.

### Рекомендация

Для ручного cutover достаточно:

1. Перед остановкой отправить служебное сообщение “maintenance 1–2 минуты” только себе/админу, если нужно.
2. Остановить старый.
3. Подождать 3–5 секунд.
4. Проверить logs: старый завершил polling loop.
5. Старт нового.
6. Первым smoke сделать `/start` и callback-кнопку, а не только `/start`.

Не надо делать blue-green для Telegram polling: это overkill.

---

## I6. `_ACTIVE_TENANT` один раз на старте — правильное допущение, но надо явно запретить in-process multitenancy

Для модели:

> 1 Telegram token = 1 process = 1 tenant

глобальный `_ACTIVE_TENANT` — нормальный, простой и безопасный выбор. Не надо сейчас строить request-scoped tenant context.

### Риск

Через 2–3 месяца кто-то может решить “а давайте одним процессом обслужим 3 клиента” и наткнуться на глобальные caches, pending, env, Notion clients, OAuth tokens, file paths.

### Рекомендация

В `tenant.py` и README явно написать:

```md
Current architecture is single-tenant-per-process.
Do not route multiple Telegram bot tokens / tenants through one Python process.
```

И добавить startup assertion: один process не принимает tenant_id из update/user/chat.

---

## I7. Full BRANDS extraction можно отложить, но минимальные leakage-тесты до Phase 3 нужны

Полный вынос `BRANDS` в 2b — разумно. Это большой refactor, и тащить его в боевую пересадку Артёма опаснее, чем оставить bridge.

Но вообще без leakage-тестов Phase 3 идти не стоит.

### Минимум до Phase 3

1. Grep-generated UI texts/logs на `maksim`, `Life Drive`, `yumsunov`, чужие handles в panferov smoke.
2. Telethon scenario `/brand` → убедиться, что показываются только `default/shoes`, а не `maksim`.
3. Проверить один end-to-end render на test token: avatar_id/voice_id из panferov env.
4. Проверить prompt hashes для `default` и `shoes`.

---

## I8. Старый unit лучше не disable сразу после первого healthcheck

Runbook говорит:

> Старый unit disable (НЕ удалять ≥1 неделю) = rollback-путь

Если disable делается сразу после старта нового, rollback всё ещё возможен, но станет на один шаг сложнее. В первые 30–60 минут после cutover лучше иметь готовый rollback script:

```bash
systemctl stop contentbot-core
systemctl start content-bot
```

А disable старого сделать после первого успешного smoke + короткого наблюдения.

### Рекомендация

- В момент cutover: old stopped, restart disabled/held only если есть риск auto-restart.
- После 1–2 часов: old disabled.
- После 1 недели: archive/remove.

---

# 🟢 Nice

## N1. Добавить `tenant doctor` CLI-команду

Пример:

```bash
python -m tenant doctor --config /root/contentbot-core/tenant.json --strict --expected panferov
```

Это удобно для runbook: проверка до `systemctl start`.

---

## N2. Печатать startup summary без секретов

При старте логировать:

```text
[tenant] id=panferov strict=1 config=/root/contentbot-core/tenant.json
[tenant] features enabled: billing, launch_monitor, youtube_broll
[tenant] features disabled: carousel, idea_bank, instagram_dm
[tenant] active_brand=default allowed_brands=default,shoes
[tenant] prompts: script_default.txt sha256=...
```

Это сильно ускорит диагностику Phase 3.

---

## N3. Сохранить snapshot effective config

После merge `BRANDS + brand_overrides + env refs` сохранить sanitized snapshot:

```text
runtime/effective_tenant_panferov.json
```

Без секретов, но с последними 4 символами provider IDs. Это поможет доказать, что бот реально стартовал с нужными настройками.

---

## N4. Добавить allowlist override-ключей

Сейчас `brand_overrides` может переопределить любой ключ в `BRANDS`.

На 2 tenant это не страшно, но лучше ограничить:

```python
_ALLOWED_BRAND_OVERRIDE_KEYS = {
  "heygen_avatar_id",
  "heygen_avatar_v4_id",
  "eleven_voice_id",
  "script_prompt_file",
  "cover_prompt_file",
  "notion_db_id",
  "tg_channel_id",
}
```

И doctor должен ругаться на неизвестные override keys.

---

# Ответы на конкретные вопросы

## Q1. Fail-open gate vs fail-closed flags — корректно или дыра?

**Корректно только для transitional no-config режима.** Это хороший bridge, чтобы не сломать текущие проды, где `tenant.json` ещё нет. Но как только `tenant.json` появился, fail-open становится дырой.

Для Phase 3 сделать strict mode:

- нет config → fail;
- фича не `true` → не показываем и блокируем;
- явный `false` → блокируем;
- неизвестная фича → fail doctor.

---

## Q2. `env:KEY` без значения → не затираем поле. Безопасно?

**Для Phase 2a — допустимо. Для Phase 3 — нет.** Это маскирует misconfig и может взять старый provider ID из кода. В strict mode `config_doctor` обязан ругаться на отсутствующие env-переменные.

---

## Q3. Достаточно ли одной точки `_brand_with_overrides`?

Для runtime provider ID — да, если проверка действительно полная. Для UI/brand picker — нет. Прямые `BRANDS[...]` чтения надо классифицировать. Блокер Phase 3 только если они могут показать/выбрать чужой бренд или повлиять на генерацию.

Минимальный фикс: `tenant.allowed_brands` и фильтр `/brand` picker.

---

## Q4. Phase 3 cutover — чего не хватает?

Не хватает token-level/process-level проверки. `pgrep -f bot.py` слабый. Нужны:

- `systemctl is-active/is-failed` старого unit;
- `MainPID=0`;
- token-lock через `flock`;
- `TENANT_ID_EXPECTED=panferov`;
- smoke не только `/start`, но и один callback + один stateful сценарий;
- rollback script, проверенный до cutover.

Webhook-сложностей нет, polling упрощает задачу.

---

## Q5. `config_doctor` — что добить до пересадки?

До Phase 3 критично:

- strict mode;
- expected tenant id;
- env refs resolved;
- prompt files exist;
- active/allowed brands exist;
- override keys allowlisted;
- state inventory files/directories checked;
- Telegram token `getMe` в runbook.

Можно отложить:

- provider live render test;
- Notion write test;
- полный вынос `BRANDS`;
- глубокие leakage-тесты всех видео.

---

## Q6. `_ACTIVE_TENANT` один раз на старте — верно?

Да. Для текущей модели это правильное упрощение. Не надо строить in-process multitenancy. Но надо явно зафиксировать architectural constraint:

> Один процесс обслуживает ровно одного tenant.

Иначе позже появится ложное ощущение, что система уже настоящая multi-tenant в одном runtime.

---

## Q7. Full BRANDS и leakage-тесты отложены — согласен?

С полным выносом `BRANDS` в 2b — согласен. Это не блокер Phase 3.

С leakage-тестами полностью до 2b — не согласен. Минимальный smoke на межбрендовые протечки до Phase 3 нужен: `/brand`, prompt hashes, provider IDs, grep чужих имён/handles в UI и тестовый E2E на test token.

---

# Recommended Phase 3 checklist

Перед cutover:

- [ ] `TENANT_STRICT=1`.
- [ ] `TENANT_ID_EXPECTED=panferov`.
- [ ] `TENANT_CONFIG=/root/contentbot-core/tenant.json`.
- [ ] `python -m tenant doctor --strict --expected panferov` green.
- [ ] Все `env:*` references resolved.
- [ ] Prompt files exist + hash logged.
- [ ] Provider IDs effective snapshot generated.
- [ ] `/brand` picker не показывает `maksim`.
- [ ] Callback gate покрывает legacy callback prefixes.
- [ ] Disabled command handlers не доступны.
- [ ] Background jobs отключённых фич не зарегистрированы.
- [ ] Old/new units имеют `Conflicts=` или token-lock.
- [ ] Rollback script протестирован на test token.
- [ ] Telethon 16–27 green на test token.
- [ ] После switch: `/start`, `/brand`, один callback, один короткий generation smoke.

---

# Итоговый CTO verdict

**Tenant-shell реализован в правильном направлении и соответствует масштабу проекта.** Не надо сейчас делать полноценную enterprise multi-tenant платформу или выносить весь `BRANDS` перед Phase 3. Но текущая реализация слишком мягкая для боевой пересадки: warning-only doctor, silent env fallback и callback-only gate могут дать тихие ошибки, которые хуже явного падения.

Я бы дал Phase 3 статус:

**GO after fixes C1–C5.**

Это небольшие изменения, не архитектурный rewrite. После них пересадка `panferov` на core как canary выглядит разумной и управляемой.
