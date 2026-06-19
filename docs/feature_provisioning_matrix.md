# Provisioning-matrix фич (черновик, A2 / 19 июня)

Продуктовая модель: контент-бот = **конструктор флагов** (`tenant.py features{}`). Для каждой опц-фичи — что технически нужно от клиента, чтобы она работала, и чьи ключи / кто платит. Цены НЕ проставлены (уточнять отдельно — правило №1).

> Колонка «Кто платит»: **клиент** = свои ключи/аккаунт; **студия** = ключи студии, биллинг «переставляем» на клиента (договор). Решается per-клиент.

| Флаг | Что технически нужно | Внешний платный сервис | Кто платит |
|---|---|---|---|
| `tg_post` | токен Telegram-бота клиента | — | клиент |
| `carousel` | — (Playwright локально) | — | — |
| `idea_bank` | Notion DB клиента (id + интеграционный токен) | Notion (free хватает) | клиент |
| `launch_monitor` | источники + web-search/LLM ключ (Gemini) | LLM API | уточнить |
| `youtube_broll` | YouTube cookies + (опц.) proxy | proxy (Webshare) | уточнить |
| `hyperframes` | Claude Code OAuth (подписка Max) + chrome-headless-shell + fonts + серверные ресурсы | Claude Max-подписка | студия (общий gen-flock) — уточнить |
| `remotion` | Claude Code OAuth + Node + Remotion-проект на сервере | Claude Max-подписка | студия — уточнить |
| `image_gen` | `FAL_KEY` (Nano Banana Pro) | fal.ai (per-image) | уточнить |
| `video_gen` | `FAL_KEY` (Kling) | fal.ai (per-video) | уточнить |
| `instagram_dm` | Meta App (`META_APP_ID/SECRET`) + webhook token + IG-аккаунт клиента | Meta (free) | клиент |
| `billing` | платёжный провайдер (ЮKassa и т.п.) если бот платный для конечных юзеров | платёжка | клиент |
| `subscriber_stats` | доступ к статистике каналов клиента | — | клиент |
| `ai_video` (Seedance) | 🔴 `FAL_KEY` (fal.ai, **платный per-clip** ~$0.11/5с) + бюджет/cost-guard | fal.ai | **уточнить — money-leak без guard** |
| `broll_pipeline` | — (Python); если включён AI_VIDEO-источник → требует ещё `ai_video`+`FAL_KEY` | — / fal.ai | — / см. ai_video |

## Зависимости между флагами
- `broll_pipeline` + AI_VIDEO-источник → требует `ai_video` (FAL_KEY). Гейт это уже закрывает (двойной callback-гейт: Seedance в Pipeline-2 блокируется по `ai_video`, даже если `broll_pipeline` включён).
- `hyperframes`/`remotion`/`ai_video` (Seedance-режиссёр) → общий Claude Code OAuth gen-flock (один lock — I5).

## TODO (доуточнить с Артёмом)
- Точные цены/лимиты сервисов (не выдумывать).
- Политика «клиент vs студия» по каждой платной фиче (fal.ai, Claude Max).
- Для `ai_video` — cost-guard параметры (I4): max clips/run, дневной бюджет, owner_only до первого клиента.
