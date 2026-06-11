# MIGRATION_STATE_INVENTORY — сервер A (panferov), Phase 0

> Снято по ssh 11 июня 2026. Для Phase 3 (пересадка panferov на core).
> Сервер A = Hetzner 65.21.154.237, бот @panferovai_contentbot, каталог /root/content-bot.

## Runtime state — что мигрирует при пересадке

| State | Где сейчас | Размер | Критичность | Мигрируем? | Валидация после копии |
|---|---|---:|---|---|---|
| `.env` (все ключи) | /root/content-bot/.env | 1.8K | HIGH | вручную → secrets.env по whitelist | config doctor: required keys |
| `billing.db` | /root/content-bot/ | **0 байт** | LOW (пустая! гейт вкл, клиентов нет) | копия для формы | sqlite open + schema check |
| `stats_history.json` | /root/content-bot/ | 384B | MED | копия | /stats показывает последний замер |
| `pub_calendar.json` | /root/content-bot/ | 373B | MED | копия | /calendar грид |
| `pending.json` | /root/content-bot/ | 2.2K | LOW | **RESET** (решение: чистый старт, активных flow на момент свитча быть не должно) | — |
| `instagram_token.json` | /root/content-bot/ | 590B | HIGH | копия | IG auth dry-check |
| `vk_token.json` | /root/content-bot/ | 317B | HIGH | копия | VK auth dry-check |
| `youtube_token.json` | /root/content-bot/ | 627B | HIGH | копия | YT auth dry-check |
| `TK_cookies_panferov.ai.json` + cookies.txt | /root/content-bot/ | 13K | MED (TikTok хрупкий) | копия | не валидируем (ручной кросспост) |
| `telethon_session.session` (+journal) | /root/content-bot/ | 41K | HIGH (uploader >20MB) | копия при ОСТАНОВЛЕННОМ процессе | uploader smoke |
| `dm_keywords.json`, `dm_log.json` | /root/content-bot/ | 3.6K | MED (IG DM-воронка) | копия | webhook smoke |
| `_stats_db_id.txt`, `_status_options.json` | /root/content-bot/ | <1K | LOW | копия (→ потом в tenant.json) | — |
| Промпты: script_prompt.txt, cover_prompt.txt, cover_prompt_shoes.txt | /root/content-bot/ | 33K | HIGH | копия + **sha256 фиксация** | hash check vs git |
| `projects/` (история карточек) | /root/content-bot/ | 927M | MED | rsync | count dirs + spot-check |
| `assets/` (аватары, шрифты) | /root/content-bot/ | 118M | HIGH | rsync | count файлов avatars/ + fonts/ |
| `broll-library/` | /root/content-bot/ | 559M | HIGH | rsync | count по категориям |
| `music/` (+tracks.json) | /root/content-bot/ | 136M | HIGH | rsync | tracks.json parse + count mp3 |
| `launch_data/` (SQLite дедуп launch monitor) | /root/content-bot/ | 2.7M | MED | копия | /launches открывается |
| `logs/`, bot.log | /root/content-bot/ | 6M | LOW | НЕ мигрируем (архив в snapshot) | — |
| `/srv/bot-media/` (nginx: постоянные медиа HeyGen, IG-хостинг) | /srv | 1.4G | HIGH | остаётся на месте (вне каталога бота) | URL smoke |
| `/srv/bot-music/`, `/srv/bot-static/`, `/srv/bot-covers/` | /srv | 137M | HIGH | остаются на месте | nginx -t + URL smoke |

## Инфраструктура (не файлы)

| Компонент | Состояние | Действие при Phase 3 |
|---|---|---|
| `content-bot.service` (systemd) | active | новый unit рядом + `Conflicts=` между old/new |
| `telethon-uploader.service` | ⚠️ **activating auto-restart (ПАДАЕТ В ЦИКЛЕ, найдено 11.06)** | разобраться ДО Phase 3 (отдельно от миграции) |
| `xvfb.service` | active (для TikTok Selenium) | сохранить |
| nginx `bot.panferov-ai.ru` | active (media/, privacy, music, proxy→8443) | конфиг не меняется (порт тот же) |
| nginx `nox.panferov-ai.ru` | active | НЕ НАША зона (Nox), не трогать |
| crontab | пустой (weekly stats живёт в JobQueue бота) | ничего |
| Telegram режим | **polling** (не webhook) | свитч = stop old → start new, lockfile |
| venv | 3.1G | НЕ мигрируем — свежий `pip install -r requirements.txt` |

## Решения, зафиксированные здесь
1. `pending.json` — RESET при пересадке (свитч в окно без активных генераций).
2. venv пересоздаётся, не копируется.
3. `/srv/*` остаётся на месте — пути не зависят от каталога бота.
4. Логи не мигрируют — уходят в snapshot старого инстанса.

## ⚠️ Найдено попутно (вне Phase 0, не чинить сейчас)
- `telethon-uploader.service` в crash-loop — проверить токен/сессию до Phase 3.
- `billing.db` пустой при включённом гейте — клиентов в биллинге нет, гейт блокирует всех кроме `ADMIN_TELEGRAM_IDS`.
