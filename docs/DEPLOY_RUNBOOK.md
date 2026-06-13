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

> Ужесточено по CTO-ревью (C4/I8/I5). ⚠️ На сервере A крутятся И content-bot,
> И Nox (оба Python) — `pgrep -f bot.py` НЕНАДЁЖЕН (поймает не тот процесс).
> Используем unit-level проверки + token-lock, НЕ pgrep.

### 0. Pre-flight (до любого stop) — всё green ИЛИ не начинаем
```
□ python -m tenant doctor --config /root/contentbot-core/tenant.json \
      --strict --expected panferov --brands default,shoes   → exit 0
□ В tenant.json: TENANT_STRICT=1, TENANT_ID_EXPECTED=panferov в окружении unit
□ Все env:* из brand_overrides реально есть в .env (doctor проверил)
□ Prompt-файлы на месте (doctor проверил) + hash залогирован
□ Telegram getMe боевым токеном → 200 (отдельная curl-проверка)
□ Telethon-матрица 16-27 на ТЕСТОВОМ токене → green
□ /brand-пикер на тестовом → только default+shoes, без maksim (leak-smoke I7)
□ rollback-скрипт rollback_panferov.sh написан И прогнан на тест-токене
```

### 1. Token-lock в новом unit (защита от двух процессов на токене)
`contentbot-core.service` → `ExecStartPre`/`ExecStart` под flock:
```
ExecStart=/usr/bin/flock -n /run/contentbot-panferov-token.lock \
    /root/contentbot-core/venv/bin/python /root/contentbot-core/bot.py
```
+ в старом и новом unit прописать `Conflicts=` друг на друга.

### 2. Остановка старого — проверяем ФАКТ, не процесс по имени
```
systemctl stop content-bot
systemctl is-active content-bot         → inactive   (НЕ pgrep!)
systemctl show content-bot -p MainPID   → MainPID=0
# на время cutover, если есть Restart=: systemctl disable --now content-bot
sleep 3-5   # дать polling-loop старого завершить getUpdates
grep -i 'polling.*stop\|shutdown' /root/content-bot/bot.log | tail -2
```

### 3. Старт нового на боевом токене
```
# боевой токен panferov → окружение contentbot-core
systemctl start contentbot-core
systemctl is-active contentbot-core     → active
journalctl -u contentbot-core -n 40     → есть «[tenant] loaded tenant_id=panferov strict=True»
                                           + startup summary (features ON/OFF, allowed_brands)
                                           нет Traceback/CRITICAL
```

### 4. Smoke после switch (НЕ только /start — I5)
```
□ /start          → меню
□ один callback   (кнопка карточки)
□ один stateful   (короткий generation — напр. /selfie до выбора музыки, без полного рендера)
□ /brand          → только default+shoes
```

### 5. Поэтапный вывод старого (I8 — rollback под рукой первые часы)
```
cutover-момент:  old stopped (+ disabled если был Restart=)
первые 1-2 часа: old НЕ удалять, rollback_panferov.sh наготове
после 1-2 ч ok:  systemctl disable content-bot
через ≥1 неделю: snapshot в архив, каталог можно убрать
```

### rollback_panferov.sh (прогнать ДО cutover на тест-токене)
```bash
#!/usr/bin/env bash
set -e
systemctl stop contentbot-core
systemctl is-active contentbot-core || true   # ждём inactive
systemctl start content-bot                   # старый юнит = rollback-путь
sleep 4
systemctl is-active content-bot               # → active
```
**Готово когда:** Артём работает на core ≥2-3 дня без критики; canary живой.
