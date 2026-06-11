# DEPLOY_RUNBOOK — contentbot (оба тенанта)

> Phase 0 артефакт (11 июня 2026). Обновляется по мере миграции.
> Релизная модель: `main → panferov (canary)`, `tag vYYYY.MM.DD-maksim.N → maksim (production)`.

## Тенанты

| | panferov (canary) | maksim (production) |
|---|---|---|
| Сервер | A: 65.21.154.237 (root) | B: 89.167.89.133 (user maksim-bot) |
| Каталог | /root/content-bot | /home/maksim-bot/maksim-bot |
| Unit | content-bot.service | maksim-bot.service |
| Ветка (переходный период) | panferov-legacy | maksim-prod |
| Деплой сейчас | scp файлов + restart | sync.sh |
| Telethon-сценарии | contentbot-tests/scenarios 16-27 | maksim-сценарии |

## Процедура деплоя (любой тенант)

```
1. PRE: pre_deploy.py 6/6 PASS локально (ruff, menu, smoke, регрессы)
2. PRE: проверка активных задач на сервере:
   pgrep -af 'ffmpeg|heygen|elevenlabs' → пусто, иначе ЖДАТЬ
3. SNAPSHOT: tar state-файлов + код (см. MIGRATION_STATE_INVENTORY таблицу)
   tar czf /root/snapshots/contentbot_$(date +%F_%H%M).tgz \
       --exclude venv --exclude projects --exclude broll-library \
       --exclude music --exclude assets /root/content-bot
4. DEPLOY: scp изменённых файлов (переходно) / git pull тегом (целевое)
5. RESTART: systemctl restart <unit>; sleep 5; systemctl is-active
6. SMOKE: tail лога на Traceback + Telethon-смок профильных сценариев
7. КРАСНЫЙ → ROLLBACK (ниже), ЗЕЛЁНЫЙ → готово
```

## Rollback

```
1. systemctl stop <unit>
2. tar xzf <последний snapshot> -C /
3. systemctl start <unit>; systemctl is-active
4. Telethon-смок
```
Время отката: минуты. Snapshot'ы хранить ≥5 последних.

## Правила
- Деплой Максиму — ТОЛЬКО тегом, только после зелёного на canary (с Phase 3).
- Никогда не рестартовать при живых ffmpeg/heygen/elevenlabs.
- Секреты не покидают сервер: deploy не трогает .env/токены.
- Один Telegram-токен = один процесс (lockfile с Phase 3, systemd Conflicts=).

## Phase 3: процедура свитча panferov на core (polling)
```
1. Новый каталог /root/contentbot-core (из main) + tenant-конфиг + state-копия
   по MIGRATION_STATE_INVENTORY (каждая строка → валидация)
2. Смок нового на ТЕСТОВОМ боте-токене (Telethon 16-27)
3. systemctl stop content-bot && pgrep -f bot.py → пусто
4. Боевой токен в конфиг нового → systemctl start contentbot-core
5. Healthcheck + смок /start + Telethon ключевых сценариев
6. Старый unit disable (не удалять ≥1 неделю) — это и есть rollback-путь
```
