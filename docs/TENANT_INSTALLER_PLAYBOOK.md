# Плейбук установки нового клиента на contentbot-core

> **Прочитай это первым (преамбула — без неё застрянешь):**
>
> 1. **Кто что делает.** Честная формула онбординга сегодня (подтверждена кодом, не маркетинг): **РАЗРАБОТЧИК** делает код-пререквизиты → **ЧЕЛОВЕК** (Артём/студия) добывает секреты во внешних сервисах руками → **ДЖУН** прогоняет серверную механику по этому чек-листу. Джун НЕ ставит клиента в одиночку. Шаги, где нужен разработчик, помечены 🔴 — на них джун останавливается и зовёт его.
>
> 2. **Где выполняются команды.** Твоя машина — Windows (PowerShell). Почти все команды плейбука — это **bash НА СЕРВЕРЕ**. Поэтому: начиная с Фазы 2 ты сначала заходишь по ssh, и ВСЕ команды с `venv/bin/python`, `systemctl`, `rsync`, `. ./.env` выполняешь УЖЕ ВНУТРИ ssh-сессии на сервере. На Windows локально они не сработают.
> ```powershell
> ssh -i $HOME\.ssh\id_rsa root@65.21.154.237
> ```
>
> 3. **Один клиент = один бот = один systemd-юнит + свой `tenant.json` + свой `.env`.** Инвариант: один процесс — один тенант (`tenant.py`, `docs/03_TENANT_MODEL.md`). НЕ доливать клиента в существующий юнит.
>
> 4. **Соседей НЕ трогать вообще.** На сервере живут: `panferov` (`/root/contentbot-core`), `legacy` (`/root/content-bot`), `Nox` (`openclaw`). Новый клиент = НОВЫЙ каталог + НОВЫЕ юниты. Сервер для тебя read-only в части соседей.
>
> 5. **Легенда режима:** 🟢 AUTO (джун выполняет по команде/чек-листу) · ✋ MANUAL (человек руками во внешнем сервисе) · 🔴 CODE (правка .py, обязателен разработчик).
>
> 6. **Правило джуна (№1, не выдумывать):** поле в анкете пустое или «уточнить» → СТОП, иди к Артёму. Не угадывай токены, домены, имена опций.

---

## ЧАСТЬ A. ПОДГОТОВКА (всё обратимо, прод не тронут — можно бросить в любой момент)

### Фаза 0. Анкета и решение по бренду

**0.1 Заполнить интейк-анкету.** Вместе с клиентом, см. отдельный документ «Анкета онбординга». WHO: Артём + клиент. Режим: ✋. Гейт: все поля **[обяз.]** закрыты; «уточнить» закрыты либо явно отложены. ~30–60 мин.

**0.2 Выбрать `tenant_id`** — короткий слаг латиницей одним словом (станет именем каталога, юнитов, `TENANT_ID_EXPECTED`). WHO: джун. Режим: 🟢. Гейт:
```bash
ssh root@65.21.154.237 'ls -d /root/<tenant_id> 2>/dev/null || echo FREE'
```
→ должно быть `FREE` и не совпадать с `panferov`/`legacy`/`nox`/`contentbot-core`/`content-bot`/`openclaw`. ~2 мин.

**0.3 🔴 РЕШЕНИЕ ПО БРЕНДУ (developer-gate, читать внимательно — здесь была главная ошибка старого плейбука).**

> **Честно и однозначно: для СОВЕРШЕННО НОВОГО клиента "путь Б без кода" НЕВОЗМОЖЕН.**
> `brand_overrides` в `tenant.json` — это слой, который МЁРЖИТСЯ ПОВЕРХ уже существующей записи в `BRANDS`-dict (`tenant.py:148-167`: берётся `dict(brand)` и накрывается оверрайдами). Если записи бренда в `BRANDS` нет — накладывать не на что, и `config_doctor` валит проверку с ошибкой `brand_overrides references unknown brand` (`tenant.py:248`). В `BRANDS` сейчас физически есть только `default`, `shoes`, `maksim` (`bot.py:435/465/612`). Нового клиента там нет.
>
> **Поэтому порядок всегда такой:**
> 1. 🔴 **Разработчик заводит запись бренда в `BRANDS`-dict** (`bot.py:434+`, эталон — блок `"maksim"` на `bot.py:612+`). Минимум — `description`, `heygen_avatar_id`/`eleven_voice_id` (можно `None`, чтобы брать из `.env`), `script_prompt_file`/`cover_prompt_file`. Если у клиента структурная специфика (`heygen_looks`, `platforms`, `notion_status_type`, `notion_rubrics`, `wardrobe_modes`) — это ТОЛЬКО в `BRANDS`, потому что этих ключей НЕТ в override-allowlist (`tenant.py:45-56`), и `config_doctor` завернёт их как `key not in allowlist` (`tenant.py:256`).
> 2. 🟢 **ПОСЛЕ этого джун может донастраивать через `brand_overrides`** только 11 плоских полей из allowlist (`tenant.py:45-56`): `heygen_avatar_id`, `heygen_avatar_v4_id`, `eleven_voice_id`, `script_prompt_file`, `cover_prompt_file`, `script_prompt_override`, `notion_db_id`, `notion_rubric_property`, `telegram_channel_handle`, `telegram_channel_display`, `description`.

WHO: Артём решает состав, разработчик исполняет `BRANDS`. Режим: 🔴. Гейт: запись бренда в `BRANDS` создана и `python -m py_compile bot.py` зелёный.
> Контекст честности: «ноль кода» сегодня не достигнут; вынос `BRANDS` в `tenant.json` отложен на ПОСЛЕ Phase 3 (`docs/03_TENANT_MODEL.md:100-104`). См. раздел «Оценка зрелости».

### Фаза 1. Внешние сервисы (всё руками человеком, ДО сервера)

**1.1 ✋ BotFather: создать бота.** `/newbot` → получить `TELEGRAM_BOT_TOKEN` (свой на клиента, инвариант one-token-one-tenant). WHO: студия/клиент. Гейт:
```bash
curl -s https://api.telegram.org/bot<TOKEN>/getMe
```
→ `"ok":true` + правильное имя. ~10 мин.

**1.2 ✋ Telegram-канал.** Добавить бота админом в канал клиента, снять числовой `chat_id` (переслать пост канала боту @userinfobot/@JsonDumpBot). WHO: джун + доступ клиента. Гейт: получен `chat_id`. ~5 мин.

**1.3 ✋ HeyGen: аватар.** Загрузить видео-исходник, обучить аватар (digital-twin, как у Максима). Забрать `heygen_avatar_id` (+ id «луков», если несколько). WHO: студия. Вход: видео клиента (требования к длительности/качеству — **уточнить по актуальным требованиям HeyGen**, в коде их нет). Гейт: avatar готов, id выписан. ~зависит от тренировки.

**1.4 ✋ ElevenLabs: клон голоса.** Загрузить аудио, склонировать, забрать `eleven_voice_id` + модель (v2/v3). WHO: студия. Вход: аудио (минуты чистой речи — **уточнить по требованиям ElevenLabs**). Гейт: voice_id выписан.

**1.5 ✋ Notion: база + интеграция + СХЕМА.** В воркспейсе клиента: создать контент-базу, создать интеграцию (`NOTION_TOKEN`), расшарить базу на интеграцию, снять `NOTION_DATABASE_ID`. **Свойства и их опции (Площадки/Рубрики/Форматы/Статусы) создаёт человек руками и они ОБЯЗАНЫ 1:1 совпасть с тем, что в `BRANDS`-конфиге клиента** (`platforms`/`notion_rubrics` — `bot.py` блок бренда) — иначе Notion молча плодит «левые» опции (`docs/03_TENANT_MODEL.md:43-45`), авто-чека нет.
> Целевой список имён опций ДАЁТ РАЗРАБОТЧИК из записи бренда в `BRANDS` (см. 0.3) — джун сверяет базу против этого списка, а не выдумывает его.
> Тип свойства «Статус»: почти всегда `select`, т.к. Notion API не создаёт опции типа `status` программно (`bot.py:448-450`). Решение status-vs-select — структурное, 🔴.
WHO: студия. Гейт: токен + db_id получены; чек-лист сверки имён код↔база пройден. ~20–40 мин.

**1.6 ✋/🔴 OAuth-приложения и ключи (только под выбранные фичи).**
- Meta App (`META_APP_ID`/`SECRET`) + привязка IG + webhook-токен — если `instagram_dm`/IG-кросспост (`crosspost.py:33-41`).
- YouTube `CLIENT_ID`/`SECRET` — если YouTube-кросспост.
- `VK_APP_ID` + redirect — если VK.
- `FAL_KEY` — если `image_gen`/`video_gen`/`ai_video`/`broll_pipeline` (платно).
- `WEBSHARE_API_KEY` + YouTube cookies — если `youtube_broll`.

> 🔴 **БЛОКЕР для ЛЮБОГО нового клиента (не только «со своим доменом»):** OAuth `redirect_uri` ЗАШИТ в код на домен **panferov** для ВСЕХ трёх площадок:
> - YouTube + Instagram (общий): `crosspost.py:29` → `https://maksim-bot.panferov-ai.ru/oauth/callback` (IG переиспользует тот же `YOUTUBE_REDIRECT_URI`, `crosspost.py:456`).
> - VK: `crosspost.py:1209` → `https://maksim-bot.panferov-ai.ru/oauth/vk/callback`.
>
> Это значит: у нового клиента OAuth уйдёт на ЧУЖОЙ callback и НЕ завершится. Нужно решение разработчика ДО Фазы 4: либо общий студийный callback-домен с маршрутизацией по tenant, либо правка `crosspost.py:29/1209` под клиента + регистрация redirect в консолях Google/Meta/VK. Без этого кросспост у нового клиента поднять нельзя.

WHO: студия/клиент по «кто платит» из анкеты; redirect — разработчик. Гейт: ключи собраны в отдельный файл `secrets.<tenant>.env`; redirect-вопрос закрыт разработчиком.

**1.7 ✋ Собрать ассеты.** Портреты владельца для обложки, B-roll фото/видео, музыка. Гейт: папки готовы локально для rsync.

### Фаза 2. Файлы конфигурации (на сервере, юнит ещё не стартует)

> Напоминание: ты уже внутри `ssh root@65.21.154.237`. Все команды ниже — bash на сервере.

**2.1 🟢 Каркас каталога.** rsync ядра из эталона panferov в новый каталог.
> Источник `/root/contentbot-core` — действующий боевой бот panferov. rsync ЧИТАЕТ источник и НЕ модифицирует его (поток в одну сторону, без `--delete` на источник). Сначала `--dry-run`.
```bash
rsync -an --exclude venv --exclude '*.env' --exclude 'secrets.*' \
  --exclude '*_token.json' --exclude '*.session' --exclude projects \
  --exclude broll-library --exclude launch_data \
  /root/contentbot-core/ /root/<tenant>/        # сначала так (dry-run), смотришь дельту
# затем без -n:
rsync -a --exclude venv --exclude '*.env' --exclude 'secrets.*' \
  --exclude '*_token.json' --exclude '*.session' --exclude projects \
  --exclude broll-library --exclude launch_data \
  /root/contentbot-core/ /root/<tenant>/
mkdir -p /root/<tenant>/broll-library/photos /root/<tenant>/broll-library/clips \
  /root/<tenant>/assets/avatars/<tenant> /root/<tenant>/launch_data /root/snapshots
```
Гейт: `ls /root/<tenant>` показывает `bot.py` + `tenant.py`. ~3 мин.

**2.2 🟢 venv.**
```bash
cd /root/<tenant> && python3 -m venv venv && venv/bin/pip install -r requirements.txt
```
> Свой venv на клиента или общий — **решение студии, уточнить** (см. open-questions). По умолчанию — свой.
Гейт: `venv/bin/python -m py_compile bot.py telethon_uploader.py` без ошибок. ~5 мин.

**2.3 🟢/🔴 Написать `tenant.json`.** **НЕ пиши с нуля — копируй готовый образец** `tenants/maksim.example.json` (или `tenants/panferov.example.json`) и правь под клиента.
```bash
cp /root/<tenant>/tenants/maksim.example.json /root/<tenant>/tenant.json
```
Поля:
- `tenant_id` = `<tenant>`.
- `features{}` — только ключи из `_KNOWN_FEATURES` (`tenant.py:57-72`), и КАЖДУЮ опц-фичу указать ЯВНО `true`/`false`. Причина: gating fail-open (`tenant.py:118-119`) — НЕ упомянутая фича остаётся живой. Чтобы выключенная фича реально отключилась, нужно `false`.
- `brands.allowed[]` — ОБЯЗАТЕЛЬНО перечислить только бренд(ы) клиента. Это жёсткий гейт пикера (`tenant.py:169-198`): без него `/brand` покажет чужие бренды (`maksim`/`shoes`) — утечка. Если `allowed` пуст/нет → пикер не ограничен.
- `brand_overrides[<brand>]` — только 11 ключей из allowlist (`tenant.py:45-56`), секреты ссылками `env:KEY`. Применять ТОЛЬКО к бренду, который разработчик уже завёл в `BRANDS` (Фаза 0.3).
WHO: джун (плоские поля) / разработчик (если правил структуру в 0.3). Гейт: см. 3.1. ~10 мин.

**2.4 🔴 (выполнено в 0.3) Запись бренда в `BRANDS`-dict.** Проверка джуна: убедись, что разработчик добавил бренд и завёл `script_prompt_<brand>.txt`/`cover_prompt_<brand>.txt`. Гейт: `py_compile bot.py` ок, `brand_overrides` ссылается на существующий бренд.

**2.5 🟢 Собрать `.env`.** Источник-шаблон — **`.env.maksim.template`** (файла `.env.template` НЕ существует, не ищи его).
```bash
cp /root/<tenant>/.env.maksim.template /root/<tenant>/.env
chmod 600 /root/<tenant>/.env
```
> **НЕ копируй `.env` другого тенанта целиком** — затащишь чужой токен/канал/Notion и бот заработает «под чужим». Бери шаблон + свои значения.

**Минимум для старта (без них бот НЕ запустится — `.env.maksim.template:7-11`):**
- `TELEGRAM_BOT_TOKEN` (из 1.1)
- `ADMIN_TELEGRAM_IDS` (chat_id владельца/студии)
- `NOTION_TOKEN` (из 1.5)
- `ANTHROPIC_API_KEY` (ядро генерации сценариев, `bot.py:108`)

**Дельта тенанта (поверх шаблона):**
- `TENANT_STRICT=1`, `TENANT_ID_EXPECTED=<tenant>`, `TENANT_CONFIG=/root/<tenant>/tenant.json`
- `DEFAULT_BRAND=<brand>`
- `TELEGRAM_CHANNEL_ID` (из 1.2), `NOTION_DATABASE_ID` (из 1.5)
- `HEYGEN_AVATAR_ID`/`ELEVENLABS_VOICE_ID` (если в бренде стоят `None` и берутся из env)
- `AUTHOR_*`, `DEFAULT_DM_REPLY_URL`
- **Переменные путей с префиксом `MAKSIM_`** — ⚠️ ОБЯЗАТЕЛЬНЫ для КАЖДОГО тенанта, несмотря на префикс `MAKSIM_` (это имя переменной в коде, а не имя клиента; `paths.py:24/35/41/49`). Минимум — `MAKSIM_BOT_ROOT=/root/<tenant>` и `MAKSIM_COVER_LIBRARY_DIR=/root/<tenant>/assets/avatars/<tenant>` (иначе селфи-обложка «Из библиотеки» молча ломается — был блокер, `CUTOVER_WINDOW_RUNBOOK.md:10`).
- per-feature ключи под включённые фичи (`META_APP_ID`, `FAL_KEY`, `WEBSHARE_API_KEY` и т.д.).
- `BILLING_ENABLED=0` (биллинг — MVP, ручное пополнение, по умолчанию выкл).
> ⚠️ Включил фичу в `features{}` → проверь, что её per-feature ключ есть в `.env`. Ключи разбросаны по 10+ модулям, gating fail-open (`tenant.py:103-119`) → без ключа фича молча не работает.
Гейт: см. 3.2. ~10 мин.

**2.6 🟢 Залить ассеты.** rsync фото/B-roll/музыки в каталог тенанта — сначала `--dry-run`, потом боевой (паттерн `CUTOVER_WINDOW_RUNBOOK.md:69-74`). Гейт: dry-run показал ожидаемую дельту.

**2.7 🔴 (первый раз) Установить 2 systemd-юнита.**
> ⚠️ Готовых `.service`-шаблонов в репозитории НЕТ (там только `_xvfb.service`). Для ПЕРВОГО клиента шаблоны генерирует разработчик (один раз извлекает из установленных panferov-юнитов на сервере и параметризует), дальше джун копирует и меняет `<tenant>`. Джун из головы unit-файл с правильными `Conflicts=`/`flock` не напишет.

Опорный шаблон (`<tenant>` подставить везде; образец env-блока — `DEPLOY_RUNBOOK.md:68-74`):
```ini
# /etc/systemd/system/<tenant>-bot.service
[Unit]
Description=contentbot <tenant>
Conflicts=panferov-bot.service legacy-bot.service
After=network-online.target
[Service]
WorkingDirectory=/root/<tenant>
EnvironmentFile=/root/<tenant>/.env
ExecStart=/usr/bin/flock -n /run/contentbot-<tenant>-token.lock \
  /root/<tenant>/venv/bin/python /root/<tenant>/bot.py
Restart=no
[Install]
WantedBy=multi-user.target
```
Аналогично `<tenant>-telethon.service` (для видео >20MB, свой ExecStart на `telethon_uploader.py`, свой flock-lock). Затем `systemctl daemon-reload`.
Гейт: `systemctl status <tenant>-bot` → loaded, inactive. ~5 мин.

---

## ЧАСТЬ B. ВАЛИДАЦИЯ И ЗАПУСК (точка невозврата — старт)

### Фаза 3. Гейты «всё зелёное ИЛИ не стартуем»

**3.1 🟢 Гейт конфига.** Выполнять ИЗ каталога тенанта (иначе модуль `tenant` не найдётся):
```bash
cd /root/<tenant>
venv/bin/python -m tenant doctor --config /root/<tenant>/tenant.json \
  --strict --expected <tenant> --brands <brand1,brand2>
```
(`tenant.py:274`). Проверяет: known keys, известность фич, типы, override-allowlist, резолв `env:*`, наличие prompt-файлов, что `brand_overrides` ссылается на существующий бренд. Гейт: `exit 0` + «OK: config valid». Любой PROBLEM → СТОП, чинить. ~1 мин.

**3.2 🟢 Гейт окружения.** Подгрузить `.env` и запустить cutover_doctor с ПОЛНЫМ набором флагов как в worked-example (`CUTOVER_WINDOW_RUNBOOK.md:82-83`, CLI `cutover_doctor.py:208-213`):
```bash
cd /root/<tenant>
set -a; . ./.env; set +a
venv/bin/python -m tools.cutover_doctor \
  --tenant <tenant> \
  --state-root /root/<tenant> \
  --bot-py /root/<tenant>/bot.py \
  --config /root/<tenant>/tenant.json \
  --billing-db /root/<tenant>/billing.db \
  --expected-instance <tenant>
```
> ⚠️ `--expected-instance` по умолчанию = `panferovai` (`cutover_doctor.py:213`). Для нового клиента дефолт оставлять НЕЛЬЗЯ — впиши `<tenant>`, иначе проверка billing-owner (`cutover_doctor.py:68`) даст ложный вердикт.
> 🔴 Плюс `_FOREIGN_MARKERS` (`cutover_doctor.py:22-26`) зашиты под panferov («maksim/lifedrive/картинг…») — для нового клиента нерелевантны, возможны ложные срабатывания. До generic-режима разработчик подтверждает: красное по чужим маркерам — не блокер. Зелёное по остальному — обязательно.
Гейт: разработчик подтвердил, что единственное красное — чужие маркеры. ~1 мин.

**3.3 🟢 getMe боевым токеном.** `curl -s https://api.telegram.org/bot<TOKEN>/getMe` → `"ok":true` + правильное имя. ~1 мин.

**3.4 🟢 Lock свободен.** `flock -n /run/contentbot-<tenant>-token.lock true && echo free`. Гейт: «free». ~1 мин.

### Фаза 4. Старт + watch

**4.1 🟢 Старт бота.** `systemctl start <tenant>-bot` → `systemctl is-active <tenant>-bot` = active, `MainPID` ненулевой, в логе строка «loaded tenant_id=<tenant> strict=True», нет Traceback / «409 Conflict» / «terminated by other». ~2 мин.

**4.2 🟢+✋ Старт telethon + ПЕРВИЧНЫЙ логин.** `systemctl start <tenant>-telethon`.
> ⚠️ Для НОВОГО клиента первичный интерактивный логин Telethon-сессии нужен ВСЕГДА (нет скопированной `.session`; в cutover её копировали из legacy — `CUTOVER_WINDOW_RUNBOOK.md:52`, у нового клиента такого нет). Это ✋: запустить telethon-логин под номером телефона клиента, ввести код подтверждения (нужен доступ к телефону клиента). Без сессии видео >20MB качаться не будет.
Гейт: telethon active, лог «connected, слушает Saved Messages», `.session` создана. ~5–15 мин.

**4.3 ✋ OAuth с нуля (новый клиент НЕ копирует чужие токены).** В Telegram-боте: `/yt_auth`, `/ig_auth`, `/vk_auth` (регистрация команд — `bot.py:21053-21072`) — живой браузерный consent, токены пишутся в `*_token.json` рядом с кодом.
> 🔴 ПРЕРЕКВИЗИТ: пока не закрыт вопрос OAuth-redirect (Фаза 1.6, `crosspost.py:29/1209`), consent уйдёт на panferov-домен и НЕ завершится. Сначала разработчик, потом этот шаг.
WHO: джун + доступ клиента к аккаунтам. Гейт: каждый `*_token.json` создан и парсится. ~10–15 мин на площадку.

**4.4 🟢 15-мин smoke-watch** (`CUTOVER_WINDOW_RUNBOOK.md:112-122`): `/start`→меню; один callback→ответ; один stateful-флоу (`/selfie` до выбора музыки, без полного рендера); `/brand`→ТОЛЬКО разрешённые бренды (нет чужих); `journalctl -u <tenant>-bot --since "8 min ago"`→нет Traceback/409; `NRestarts=0`. Гейт: все пункты зелёные. ~15 мин.

### Фаза 5. Go-live

**5.1 🟢 Тест >20MB (опц.).** Видео с #crosspost в Saved Messages → telethon скачивает в `projects`. ~5 мин.

**5.2 🟢 Закрепить через 2–3 дня стабильности.** Заменить `Restart=no`→`Restart=always` обоим юнитам + `systemctl enable <tenant>-bot <tenant>-telethon` + `daemon-reload`. Гейт: после рестарта сервера боты поднимаются. ~5 мин.

**Откат (для НОВОГО клиента — простой):** новый юнит изолирован. `systemctl stop <tenant>-bot <tenant>-telethon` и не включать. НЕ копировать cutover-rollback (у нового клиента нет legacy для возврата). Соседи не трогаются.

---

## DEFINITION OF DONE
- [ ] 🔴 разработчик: запись бренда в `BRANDS` заведена; OAuth-redirect решён; .service-шаблоны сгенерированы; cutover_doctor по чужим маркерам подтверждён как не-блокер
- [ ] `.env` содержит 4 минимальных ключа: `TELEGRAM_BOT_TOKEN`, `ADMIN_TELEGRAM_IDS`, `NOTION_TOKEN`, `ANTHROPIC_API_KEY`
- [ ] `.env` НЕ скопирован у другого тенанта; `chmod 600` на `.env` и `secrets.<tenant>.env`
- [ ] `MAKSIM_BOT_ROOT` и `MAKSIM_COVER_LIBRARY_DIR` заданы на `/root/<tenant>/...`
- [ ] `tenant doctor` exit 0 + `cutover_doctor` зелёный (кроме чужих маркеров) с `--expected-instance <tenant>`
- [ ] getMe боевым токеном = ok, lock свободен
- [ ] оба юнита active, лог «loaded tenant_id=<tenant> strict=True», нет Traceback/409
- [ ] `brands.allowed` задан; `/brand` показывает ТОЛЬКО бренды клиента
- [ ] каждая опц-фича в `features{}` указана ЯВНО true/false; у каждой true-фичи есть per-feature ключ
- [ ] Telethon `.session` создана (первичный логин выполнен)
- [ ] OAuth `*_token.json` валиден для каждой активной площадки
- [ ] 15-мин watch зелёный, NRestarts=0
- [ ] зафиксировано «кто платит» по каждой платной фиче
