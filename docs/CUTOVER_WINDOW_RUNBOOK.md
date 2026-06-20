# CUTOVER WINDOW RUNBOOK — panferov → core (паритет)

> Сгенерировано 2026-06-20 после многоагентного аудита готовности (run wf_7ee9c298-18d, вердикт **GO-with-fixes**).
> Сервер: Hetzner 65.21.154.237 (root). LEGACY=`/root/content-bot` (unit `content-bot.service` + `telethon-uploader.service`). CORE=`/root/contentbot-core` (units `contentbot-core.service` + `contentbot-core-telethon.service`, оба установлены, inactive). Nox=`openclaw` — НЕ ТРОГАТЬ.
> Все команды — на сервере как root. `SRC=/root/content-bot`, `DST=/root/contentbot-core`.

## ✅ Уже сделано ДО окна (прод не тронут)
- pre-rsync bulk 1.8G (projects/assets/broll-library/music) legacy → core.
- `DST/tenant.json` (паритет, billing=false, allowed=[default,shoes], без overrides) + `DST/.env` (копия legacy + TENANT_STRICT/ID_EXPECTED/CONFIG, META_APP_ID, BILLING_ENABLED=0, DEFAULT_BRAND снят).
- **Блокер #2 закрыт:** `MAKSIM_COVER_LIBRARY_DIR=/root/contentbot-core/broll-library/photos` в `DST/.env` (селфи-обложка «Из библиотеки»).
- **Блокер telethon закрыт:** `telethon==1.42.0` установлен в core venv (+ в `requirements.txt`) — иначе `contentbot-core-telethon.service` падал на импорте (поймала дельта-верификация; `py_compile` это НЕ ловит).
- `mkdir -p /root/snapshots`.
- Установлены инертные юниты core (bot + telethon) с `Conflicts=` + flock + Restart=no.
- Snapshot-скрипт `DST/scripts/cutover_snapshot_v2.sh` (оба формата: .tgz + flat+MANIFEST).
- `cutover_doctor` = **EXIT 0 GREEN** (2 WARN = срез C, движки OFF).
- **Boot-репетиция пройдена (тест-токен):** core стартует чисто — `tenant=panferov strict=True`, features ON/OFF паритетные, `allowed=[default,shoes]`, Scheduler up, NRestarts=0. (8443 был занят legacy — ожидаемо; в окне legacy остановлен первым.)

## ⚠️ Предусловия окна (от Артёма)
- Окно без активных генераций/диалогов (pending RESET) — STEP 0 проверяет.
- VK — пропускаем (сломан: нет VK_APP_ID; OFF на паритете; фикс в срезе C).
- IG-токен истекает ~29 июня — отдельно (после: `/instagram_auth` в core).
- ✅ Boot-ядра под тест-токеном прогнан (стартует чисто). Логика `rollback_panferov.sh` (стоп нового → старт старого, гейт MainPID=0) проверена статикой; путь отката простой (legacy-юнит не трогается).

---

## ФАЗА 4 — стоп legacy + миграция state

```bash
SRC=/root/content-bot ; DST=/root/contentbot-core

# 4.0 STEP 0 — подтвердить, что нет активных FSM-флоу (иначе их потеряем при RESET pending)
python3 -c "import json;d=json.load(open('$SRC/pending.json'));a=[(u,v.get('state')) for u,v in d.items() if v.get('state')];print('ACTIVE FLOWS:',a);import sys;sys.exit(1 if a else 0)" \
  && echo '[ok] pending idle' || echo '[WARN] есть активный флоу — подтвердить потерю у Артёма'
pgrep -af 'ffmpeg|remotion|hyperframes|puppeteer|yt-dlp' | grep -v pgrep || echo '[ok] нет тяжёлых процессов'

# 4.1 Стоп ОБА legacy-демона (bot + telethon-uploader). Explicit stop гасит Restart=always.
systemctl stop content-bot.service
systemctl stop telethon-uploader.service
# Жёсткий гейт: дождаться MainPID=0 (освобождает Telegram polling + порт 8443 + telethon_session)
until [ "$(systemctl show content-bot.service -p MainPID --value)" = "0" ]; do sleep 1; done
until [ "$(systemctl show telethon-uploader.service -p MainPID --value)" = "0" ]; do sleep 1; done
systemctl is-active content-bot.service telethon-uploader.service   # → inactive inactive
echo "[ok] legacy остановлен"

# 4.2 Snapshot legacy (страховка отката) — после стопа, файлы тихие
bash $DST/scripts/cutover_snapshot_v2.sh
# прим.: реальная legacy billing-база = $SRC/billing/billing.db (~57K); snapshot/--restore-state по billing рассинхрон.
# Для свитча неважно (billing OFF, плоский откат legacy не трогает → его billing цел). --restore-state НЕ использовать.

# 4.3 OAuth-токены (HIGH) — те же имена, core читает Path(__file__).parent
cp -p $SRC/instagram_token.json $DST/
cp -p $SRC/youtube_token.json   $DST/
# vk_token.json НЕ копируем (мёртвый). pending.json НЕ копируем (RESET).

# 4.4 Telethon-сессия (HIGH) — копировать ПОСЛЕ стопа telethon-uploader (была живой)
cp -p $SRC/telethon_session.session $DST/
[ -f $SRC/telethon_session.session-journal ] && cp -p $SRC/telethon_session.session-journal $DST/ || true

# 4.5 IG DM воронка + stats/calendar + tiktok (MED)
for f in dm_keywords.json dm_log.json .dm_reply_state.json stats_history.json pub_calendar.json \
         TK_cookies_panferov.ai.json cookies.txt yt_task.json; do
  [ -f $SRC/$f ] && cp -a $SRC/$f $DST/ || true
done

# 4.6 launch_data — ТОЛЬКО по имени (НЕ затирать core youtube_channels.json — он свежее)
cp -a $SRC/launch_data/seen_launches.db  $DST/launch_data/
cp -a $SRC/launch_data/owner_chat_id.txt $DST/launch_data/

# 4.7 Права
chmod 600 $DST/instagram_token.json $DST/youtube_token.json $DST/telethon_session.session $DST/.env 2>/dev/null || true
chown root:root $DST/*.json $DST/telethon_session.session 2>/dev/null || true

# 4.8 Final-delta rsync bulk — СНАЧАЛА dry-run (itemize: видно что реально перенесётся/удалится)
# ⚠️ broll-library/photos = каталог MAKSIM_COVER_LIBRARY_DIR. Если dry-run покажет '*deleting' внутри
#    broll-library/photos (panferov-only cover-фото, которых нет в legacy) — НЕ запускай --delete по broll-library.
for d in projects assets broll-library music; do
  echo "=== dry $d ==="
  rsync -an --delete --itemize-changes "$SRC/$d/" "$DST/$d/" | grep -E '^(\*deleting|>f|cd)' | head -20 || echo "[ok] $d: дельты нет"
done
# Если дельта приемлема (нет неожиданных *deleting):
for d in projects assets broll-library music; do rsync -a --delete "$SRC/$d/" "$DST/$d/"; done
```

## ФАЗА 5 — валидация перед стартом (read-only)

```bash
cd $DST
set -a; . ./.env 2>/dev/null; set +a
venv/bin/python -m tools.cutover_doctor --tenant panferov --state-root $DST --bot-py $DST/bot.py \
  --billing-db $DST/billing/billing.db --config $DST/tenant.json --expected-instance panferovai; echo "doctor exit=$?"   # → 0
venv/bin/python -m py_compile bot.py telethon_uploader.py && echo "[ok] compile"
# ⚠️ py_compile НЕ ловит отсутствие модуля. Импорт-смок telethon (демон >20MB):
venv/bin/python -c "from telethon import TelegramClient, events; import telethon; print('[ok] telethon', telethon.__version__)"   # → 1.42.0
python3 -c "import sqlite3;print('seen_launches:',sqlite3.connect('$DST/launch_data/seen_launches.db').execute('PRAGMA integrity_check').fetchone()[0])"
python3 -c "import json;[json.load(open('$DST/'+f)) for f in ('instagram_token.json','youtube_token.json')];print('[ok] tokens parse')"
ls -la $DST/*token*.json $DST/telethon_session.session
flock -n /run/contentbot-panferov-token.lock true && echo "[ok] lock свободен"
```

## ФАЗА 6 — старт core + watch

```bash
# 6.1 Перепроверить, что legacy полностью лёг (гонка за 8443/токен)
until [ "$(systemctl show content-bot.service -p MainPID --value)" = "0" ]; do sleep 1; done

# 6.2 Старт core-бота (Conflicts= автоматически держит legacy остановленным)
systemctl start contentbot-core.service
systemctl is-active contentbot-core.service                      # → active
systemctl show contentbot-core.service -p MainPID --value        # → ненулевой
tail -n 50 $DST/bot.log    # → tenant_id=panferov strict=True, движки OFF, allowed=default,shoes; НЕТ Traceback/'Conflict'/'terminated by other'

# 6.3 Старт core-telethon (Conflicts= держит legacy-uploader остановленным)
systemctl start contentbot-core-telethon.service
systemctl is-active contentbot-core-telethon.service             # → active
tail -n 20 $DST/telethon_uploader.log                            # → connected, слушает Saved Messages

# 6.4 Sanity окружения
ss -ltnp | grep 8443 || echo "[warn] IG DM webhook не забинден (не блокер паритета)"
systemctl is-active openclaw                                     # → active (Nox цел)

# 6.5 15-МИН WATCH (Telegram smoke)
#  T+0  journalctl -u contentbot-core -n 60 --no-pager → tenant loaded panferov strict; нет Traceback/Conflict
#  T+1  /start → меню
#  T+2  один callback (кнопка карточки) → ответ, журнал чист
#  T+4  /brand → ТОЛЬКО default + shoes (нет maksim/lifedrive)
#  T+6  /selfie до выбора музыки (СТОП до публикации) → стейт двигается, журнал чист
#  T+8  journalctl --since "8 min ago" → нет Traceback, polling здоров (нет 409)
#  T+12 systemctl show contentbot-core -p NRestarts --value → 0 (не флапает)
#  T+15 GREEN → оставить core, legacy STOPPED, rollback взведён 1-2 ч
#  (Опц.) тест >20MB: видео с #crosspost в Saved Messages → core-telethon скачивает в DST/projects

# 6.6 ABORT/ROLLBACK (если любой критерий: core не active / NRestarts>0 / Traceback / 409 Conflict /
#      tenant line нет или !=panferov / /start без меню ~60с / /brand течёт чужой бренд)
#   bash $DST/scripts/rollback_panferov.sh           # ПЛОСКИЙ откат, БЕЗ --restore-state (гасит core-bot → поднимает legacy-bot)
#   ⚠️ ОБЯЗАТЕЛЬНО 2-м шагом (rollback-скрипт НЕ трогает telethon-юниты!):
#   systemctl stop contentbot-core-telethon.service; systemctl start telethon-uploader.service   # вернуть legacy-uploader
#   # legacy /root/content-bot во время watch не трогался → его state цел
#   # --restore-state нужен ТОЛЬКО если core писал в legacy-пути: find $SRC -newer <snap> -type f
```

## После окна (когда core стабилен ≥2-3 дня)
- `contentbot-core.service` + `contentbot-core-telethon.service`: добавить `Restart=always`, `systemctl enable`.
- `systemctl disable content-bot telethon-uploader` (legacy), через ≥неделю — архив snapshot + удалить каталог.

## Открытые вопросы Артёму (из аудита)
1. Окно без активных флоу — STEP 0 проверит; ок терять незавершённый?
2. IG-токен ~29 июня: освежить ДО окна или принять остановку IG через неделю (потом `/instagram_auth`)?
3. Длительность watch (по умолч. 15 мин + rollback 1-2 ч) — норм?

## Срез C (после свитча, отдельно — НЕ в окне)
- VK: добавить `VK_APP_ID`, поправить redirect-домен (`crosspost.py:1209` maksim-bot→bot), `/vk_auth` заново.
- HF/Remotion бренд-адаптация (`reference_pack.panferov.md`, `auto_broll.py` промпт) ДО включения движков.
- shoes-лук «Жёлтая рубашка» (вернуть `yellow_shirt` в core BRANDS) — код-фикс.
- `INSTAGRAM_TARGET_USERNAME=<panferov ig>` запинить до будущего re-auth IG (если у FB >1 IG).
