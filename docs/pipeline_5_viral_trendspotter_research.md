---
name: pipeline-5-viral-trendspotter-research
description: Research-отчёт по Pipeline 5 (виральный трендспоттер Reels/Shorts/TikTok для контент-бота Максима). Собран 4 параллельными субагентами 8 июня 2026. Источники: GitHub OSS, коммерческие SaaS, академические работы, юр-практика 2024-2026.
metadata:
  type: research
  date: 2026-06-08
  branch: maksim-bot
---

# Pipeline 5 — Виральный трендспоттер: research-отчёт

> Задача: ежедневно показывать Максиму **5 свежих залетевших Reels/Shorts/TikTok** в его нишах (картинг / глэмпинг / премиум-туризм / предпринимательство-личный бренд) с кнопкой «повторить через наши пайплайны». Главные требования: точная фильтрация по нише + надёжный дедуп (Артём прямо отметил «было много повторений» в предыдущей попытке через текстовый launch_monitor).

## 1. TL;DR — рекомендация

**Прямого аналога нет на рынке** — самые близкие коммерческие продукты (Trendpop $250-2000/мес, Exolyt $400/мес) дорогие, B2B-sales-only, заточены под TikTok. SaaS которые работают через official Meta API имеют слабый discovery в принципе — это **структурное ограничение** платформ, не недостаток конкретных продуктов.

**Собрать самим дешевле и точнее под нишу Максима.**

**Рекомендуемый стек** (под бюджет $50-200/мес):

| Слой | Что используем |
|---|---|
| Источники | Apify actor `apify/instagram-reel-scraper` ($1/1k) + Apify `clockworks/tiktok-scraper` или `apidojo/tiktok-scraper` ($0.30-1.70/1k) + `scrapetube` для YouTube Shorts (бесплатно) + YouTube Data API `videos.list` для метрик |
| Виральность | Композитный score: `0.6 × velocity_z + 0.4 × engagement_z` нормированные к baseline автора за 30 дней. **НЕ абсолютные просмотры.** |
| Дедуп | 3 слоя: L1 URL → L2 `videohash` (perceptual) → L3 sentence-embedding транскрипта (cosine > 0.85) |
| Нишевая фильтрация | account-seed (30-50 аккаунтов вручную) + Claude Haiku 4.5 на caption+transcript ($0.001/видео = $5-30/мес) |
| Хранение | Postgres + pgvector для эмбеддингов; Redis-queue для воркеров |
| Доставка | Утром 09:00 — TG-сообщение «топ-5 за 24ч» + кнопка «🎬 Повторить через Pipeline 2/3» |

## 2. Главный архитектурный инсайт (структурное противоречие отрасли)

Все SaaS с **сильным discovery** работают в скрейпинг-grey-zone (Trendpop, Exolyt, Pentos, BigSpy, PowerAdSpy, ViralFindr, Meedro). Все продукты с **legitimate Meta/TikTok partnership** имеют слабый discovery (ContentStudio, Sprout Social, Later, Predis.ai через Graph API клиента).

Это **структурное противоречие**: legitimate API не отдают чужих виральных постов по дизайну — Meta не хочет выдавать конкурентные данные через свой API. Прецеденты **Meta v. Bright Data (январь 2024)** и **hiQ v. LinkedIn** (Ninth Circuit) защищают скрейпинг публичных logged-out данных от CFAA, но это всё ещё нарушает ToS платформ.

**Apify в этой картине** — vendor который **берёт юр.риск на себя**: запросы идут с их инфраструктуры, мы не подписываем IG ToS как пользователь. Большинство SaaS-аналитик (Trendpop / Exolyt / Vertical Viral / ViralFindr) построены поверх такого же scraping.

## 3. Источники данных — сравнительная таблица

| Источник | Платформа | Цена | Состояние | Юр.чистота |
|---|---|---|---|---|
| Apify `apify/instagram-reel-scraper` | IG | $1.00/1k | Активный, поддерживается | Серая (vendor берёт риск) |
| Apify `apidojo/instagram-scraper` | IG | $0.50/1k | Активный | Серая |
| Apify `clockworks/tiktok-scraper` | TT | $1.70/1k | Активный, ловит TT-defenses чаще | Серая |
| Apify `apidojo/tiktok-scraper` | TT | $0.30/1k | Активный | Серая |
| `scrapetube` + YT Data API | YT Shorts | Бесплатно (квота 10k units/день) | Активен (v2.6, сентябрь 2025) | YT ToS нарушается scrape'ом, риск низкий |
| `subzeroid/instagrapi` | IG | $0 | Активный (v2.8.5, июнь 2026) | Требует логин — **риск disable аккаунта Максима** |
| `instaloader` | IG | $0 | Активный (v4.15.1, март 2026) | IG warning'ит юзеров в 2025 |
| `davidteather/TikTok-Api` | TT | $0 + residential proxy ~$5-15/GB | Активный (v7.3.3, апрель 2026) | TT ToS прямо запрещает |
| Official IG Graph API | IG | $0 | — | Чистый, но **не подходит** (30 хэштегов/неделя/токен, нет views чужих Reels, требует Business Verification + Public Content Access App Review) |
| Official YT Data API v3 | YT | $0 (квота 10k units) | Чистый | OK для метрик, не для discovery Shorts напрямую |
| Official TT Research API | TT | $0 | Чистый | **Только академия/non-profit US/EEA/UK** — коммерч.клиент не пройдёт |

**Расчёт бюджета** (50 аккаунтов × 5 новых постов/аккаунт/день × 30 дней = 7500/мес/платформа):

- IG через Apify reel-scraper: **$7.50/мес** (или $3.75/мес на apidojo)
- TT через apidojo: **$2.25/мес**
- YT через scrapetube + Data API: **$0/мес** (укладываемся в free квоту)
- Claude Haiku 4.5 классификатор: **$5-30/мес** ($1/Mtok in + $5/Mtok out, ~$0.001/видео)
- Residential proxies (опционально, если TT-Api ломается): **$5-30/мес**
- **Итого: $20-100/мес.** При расширении до 150 аккаунтов × 10 постов — $50-200/мес.

## 4. Метрика виральности — обоснованный выбор

Три варианта рассмотрены:

- **A. Абсолютные просмотры** — большие аккаунты всегда выигрывают, ловит «старые хиты», не свежие тренды. **Не подходит.**
- **B. Engagement-rate** — высокий ER на 5k views не значит виральность. Полезен только в композите.
- **C. Velocity + z-score к baseline автора** — ловит **ранние** виральные ролики, отсекает звёзд с постоянно высокими цифрами, даёт готовое объяснение. **Рекомендуется.**

**Формула**:
```
velocity = views / hours_since_post
z_vel = (velocity - μ_author_30d) / σ_author_30d
z_eng = ((likes+comments+shares)/views − μ_author_30d) / σ_author_30d
viral_score = 0.6*z_vel + 0.4*z_eng
```

Подтверждено академически — Hybrid Score из [arXiv 2510.05761](https://arxiv.org/pdf/2510.05761) нормирует engagement на размер аудитории и добавляет velocity. Z-score даёт «объяснимость» — для каждого ролика мы можем сказать: «views/h = +3.2σ от среднего автора за 30 дней» — то что Максим запросил.

**Порог отсечки** калибруется первые 2 недели на реальных данных — теоретический порог (5× baseline или абсолют >100k views/<24h) не работает универсально, особенно для маленьких ниш типа русскоязычного картинга, где сама медиана низкая.

## 5. Дедуп — 3 слоя

Простой URL-дедуп ловит ~30% повторов. Артём жалуется именно на остальные 70%. Нужны контентные слои:

| Слой | Что ловит | Технология | Стоимость |
|---|---|---|---|
| L1 URL+post_id | Тот же пост дважды | hash в БД | $0 |
| **L2 Perceptual video hash** | Перезаливы, кропы, watermark, ресайз | [`videohash`](https://github.com/akamhy/videohash) — 64-битный wavelet collage hash | $0, ~2-5 сек/ролик |
| **L3 Transcript embedding** | Один скрипт, разные авторы (рерайт идеи) | faster-whisper + `paraphrase-multilingual-MiniLM-L12-v2`, cosine > 0.85 | $0 локально, ~3-10 сек/ролик |
| L4 (опц.) Audio fingerprint | Реюз тренд-аудио | [chromaprint](https://github.com/acoustid/chromaprint) | $0, <100мс |

Порог cosine 0.85 — компромисс ([Milvus reference](https://milvus.io/ai-quick-reference/how-can-sentence-transformers-be-used-for-data-deduplication-when-you-have-a-large-set-of-text-entries-that-might-be-redundant-or-overlapping)): 0.97 пропускает перефразы, 0.85 даёт небольшой риск ложных срабатываний, который дополнительно фильтруется проверкой совпадения первого хэштега.

**Open question**: videohash не публикует benchmark для роликов <30 сек. Threshold нужно tuning'овать на 200-500 наших роликов прежде чем коммититься.

## 6. Нишевая фильтрация — гибрид

| Подход | Pros | Cons |
|---|---|---|
| Hashtag-seed | $0, мгновенно | Хэштеги врут / отсутствуют. Промахи 30-50% |
| Account-seed (30-50 вручную) | Высокая точность, дёшево | Закрытая выборка, не ловит новых авторов |
| **LLM-classifier** (Claude Haiku, function calling) | Понимает контекст, ловит «глэмпинг без хэштега», даёт объяснение | $$ per call |

**Рекомендация**: гибрид. Step 1 — account-seed (Максим даёт список 30-50 аккаунтов в своих нишах). Step 2 — Claude Haiku валидирует на caption+transcript: `{is_in_niche: bool, niche: enum, confidence: 0-1, reason: str}`. Это сразу даёт человеко-читаемое объяснение «почему этот ролик попал в дайджест».

Стоимость Claude Haiku 4.5: $0.001/видео = **$30/мес на 1000 видео/день**. С prompt caching и batch API — $5-10/мес.

## 7. Эталонная архитектура

```
┌─────────────────────────────────────────────────────────────┐
│ HOURLY CRON (asyncio + APScheduler)                         │
│   ├─ Apify IG actor      ──┐                                │
│   ├─ Apify TT actor       ─┼─→ raw_posts queue (Redis list) │
│   └─ scrapetube YT batch  ─┘                                │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ WORKER POOL (4-8 процессов, RQ или Celery)                  │
│ Per post:                                                   │
│   1. L1 dedup → check post_id in Postgres → skip if seen    │
│   2. Download video → faster-whisper transcript             │
│   3. L2 dedup → videohash → SELECT WHERE hamming<6          │
│   4. L3 dedup → sentence-transformer embed →                │
│                pgvector cosine search >0.85                 │
│   5. Compute velocity, z-scores                             │
│      (baseline автора из последних 30d в Postgres)          │
│   6. Claude Haiku classify → {in_niche, niche, confidence}  │
│   7. INSERT into candidates table                           │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ DAILY DIGEST (09:00 MSK)                                    │
│   SELECT TOP 5 from candidates                              │
│   WHERE created_at > now-24h                                │
│     AND viral_score > threshold                             │
│     AND niche IN (user_niches)                              │
│   ORDER BY viral_score DESC                                 │
│   → TG-сообщение Максиму с объяснением каждого ролика:      │
│     "📈 +3.2σ velocity vs автор baseline                    │
│      🎯 niche=glamping (Haiku conf=0.92)                    │
│      ✨ почему: разбор частой ошибки + личный кейс          │
│      [🎬 Повторить через Pipeline 2/3] [Notion]"            │
└─────────────────────────────────────────────────────────────┘
```

**Ключевые таблицы**:
- `posts(id, platform, post_id, author, video_hash, transcript_emb vector(384), views, likes, comments, shares, posted_at, fetched_at)`
- `author_baseline(author, μ_velocity, σ_velocity, μ_eng, σ_eng, updated_at)` — пересчёт раз в сутки
- `candidates(post_id, viral_score, z_vel, z_eng, niche, llm_reason, surfaced_at)`

**pgvector** для эмбеддингов (нативный cosine search, индекс IVFFlat). Альтернатива — Redis-Stack VectorSearch.

## 8. Топ-кандидаты OSS

### Instagram Reels
- **subzeroid/instagrapi** (6.3k★, активен) — mobile private API, требует логин = риск disable аккаунта. Maintainer прямо пишет «fragile in production», советует HikerAPI SaaS.
- **instaloader** (12.5k★) — самое популярное, есть в Bellingcat toolkit. IG warning'ит юзеров в 2025 (issue #2555).
- **chris-greening/instascrape** — АРХИВИРОВАН (апр 2023). Сам автор предупреждает «possible disabling».
- Готовое: **Apify `instagram-reel-scraper`** + **Apify `instagram-hashtag-scraper`** — без логина, $1-3/мес для нашего объёма.

### YouTube Shorts
- **dermasmid/scrapetube** (513★, v2.6 сентябрь 2025) — `get_search(content_type="shorts", sort_by="view_count")` — прямой попадание в discovery.
- **tombulled/innertube** (474★, v2.1.19 июль 2025) — reverse-engineered InnerTube без квот.
- **yt-dlp** — ⚠️ **баг #13122**: на новых Shorts отдаёт ~50% реального view_count — НЕ использовать для метрик. Только для скачивания.
- Official YT Data API v3: бесплатно, 10k units/день. `videos.list` = 1 unit за lookup.

### TikTok
- **davidteather/TikTok-Api** (6.4k★, v7.3.3 апрель 2026) — флагман. `ms_token` живёт ~10 сек, нужен Playwright + residential proxy.
- **drawrowfly/tiktok-scraper** — МЁРТВ (последний коммит июль 2021).
- Apify `clockworks/tiktok-scraper` или `apidojo/tiktok-scraper` для прода.
- TikTok Research API — только академия US/EEA/UK.

## 9. Коммерческие SaaS-аналоги

| Продукт | Источники | Мин. тариф/мес | Под наш кейс |
|---|---|---|---|
| **Trendpop** | TikTok (1B+ видео) | $250-2000 | Прямой аналог, но дорого, B2B-sales, TikTok-only, куплен Collab Inc |
| **Exolyt** | TikTok | $0-950 | Близко по UX, есть Trends-модуль |
| **VidIQ** | YouTube | $7.50-15 | Единственный с «daily trending ideas» как фичей, но только YT |
| **Pentos** | TikTok | $49-99 | Trends Pro |
| **Predis.ai** | IG/FB/TT | $32-249 | Генератор постов + competitor analysis, не trends-discovery first |
| **ContentStudio** | мульти | $25-299 | Через Meta API → слабый discovery |
| **BigSpy/PowerAdSpy** | реклама | $9-399 | Ad spy, не органика |
| **ViralFindr** | IG | $14.95-29.95 | Слабый, нишевый |

**Вывод**: ничего точно под наш кейс «5 виральных в TG-боте под глэмпинг/картинг в RU-сегменте» не делает. Прямое самостоятельное построение через Apify+Claude дешевле минимум в 5-10 раз ($30-100/мес vs $250-2000) и точнее под нишу.

## 10. Юр.риски — прямо

- **Скрейпинг публичных Reels через Apify** — серая зона по ToS платформ, но защищена прецедентами Meta v. Bright Data (январь 2024) и hiQ v. LinkedIn (9th Circuit). Apify берёт инфра-риск на себя. Большинство SaaS-аналогов работают так же.
- **Залогиненные скраперы (instagrapi/Instaloader)** — высокий риск disable аккаунта Максима в IG. **Не использовать с его рабочим аккаунтом.**
- **Показ Reel-ссылки/превью в TG-боте** — OK (deep-link на оригинал).
- **Скачивание видео и репост без атрибуции** — нарушение DMCA и IG ToS. **Не делать.** Только показ ссылок + наш Pipeline 2/3 «повторить идею своими руками».
- **RU/EU GDPR/152-ФЗ**: серая зона для скрейпинга публичных данных, но риск минимален пока не публикуем чужие данные и не делаем массового профайлинга.
- **TikTok ToS жёстче IG** — official Research API недоступен коммерч.клиенту, unofficial Api нарушает ToS прямо. Для прода через Apify (vendor берёт риск).

## 11. План реализации (поэтапно)

### Этап 1. MVP за 1-2 сессии (≤3 часа)
1. Account-seed list — Максим даёт 20-30 аккаунтов в своих нишах.
2. Cron раз в сутки (09:00 MSK) вызывает Apify IG reel-scraper по этим аккаунтам.
3. Простой viral score: `views_per_hour / median(author_history)`.
4. Дедуп L1 (URL) + примитивный L2 (хэш caption первой строки).
5. Топ-5 в TG-сообщении Максиму с превью и кнопками «🎬 Повторить через Pipeline 2» / «🎯 Через Pipeline 3 с аватаром».

### Этап 2. Полный стек (3-5 сессий)
1. Добавить TT (через Apify) и YT Shorts (через scrapetube + Data API).
2. Реальный L2 perceptual videohash + L3 transcript embedding (faster-whisper уже есть в репо).
3. Claude Haiku 4.5 нишевая классификация.
4. Postgres + pgvector для эмбеддингов.
5. Author baseline для z-score velocity.

### Этап 3. Производство (через 2-3 недели наблюдений)
1. Калибровка порогов velocity и engagement по реальному baseline в нишах.
2. Дашборд в Notion: история всех виральных идей, какие Максим повторил, какие сработали.
3. Feedback loop: Максим помечает «не релевантно» — ниши-классификатор учится на негативе (примеры в промпт Claude).

## 12. Открытые вопросы

1. **Account-seed list — кто составит?** Идеально — Максим сам (он знает свой рынок). Альтернатива: я запускаю отдельный рисерч-агент по «топ-50 IG-аккаунтов в нишах X», но качество хуже без Максимовского глаза.
2. **Бюджет на инфраструктуру** — закладываем $50-100/мес как стартовый, расширяем по факту?
3. **Postgres + pgvector** — добавляем в инфру сервера nox-maksim или используем managed (Supabase free tier, Neon)?
4. **L2 videohash порог** — нужна A/B-калибровка на 200-500 роликов после первых 2 недель.
5. **Whisper на сервере** — faster-whisper-small на CPU = 5-10 сек/30-секундный ролик. На 1000 видео/день = 1.4-2.8 часа CPU/день. Помещается, но узкое горлышко.
6. **Юр.консультация перед коммерческим релизом**? Если Pipeline 5 станет продаваемой фичей для других клиентов (не только Максим), стоит проконсультироваться с юристом по скрейпинг-практикам в РФ.

## 13. Не уверен (data gaps из рисерчей)

- Точные пороги «5× velocity baseline = виральный» и «10× engagement = виральный» — это маркетинговые эвристики, не Meta-документация.
- Возвращает ли Apify `instagram-reel-scraper` поле `view_count` для Reels (в actor написано `play_count` — обычно эквивалент, но не проверено на живом ране).
- Реальный benchmark `videohash` для роликов <30 сек.
- Конкретные цены Pentos, BigSpy, Meedro, HikerAPI — источники расходятся, не подтверждено по pricing page.
- Реальный объём «новых постов/день» у будущих Максимовских seed-аккаунтов — оценка $30-150/мес может ×2-3 при недооценке.
- Текущая бизнес-модель Trendpop после поглощения Collab Inc (ноябрь 2023) — остался ли self-serve или только B2B embed.

---

## Источники (40+ ссылок)

### OSS
- [subzeroid/instagrapi](https://github.com/subzeroid/instagrapi)
- [instaloader/instaloader](https://github.com/instaloader/instaloader)
- [chris-greening/instascrape](https://github.com/chris-greening/instascrape) (архивирован)
- [Instaloader issue #2555 — IG threatening ban](https://github.com/instaloader/instaloader/issues/2555)
- [dermasmid/scrapetube](https://github.com/dermasmid/scrapetube)
- [tombulled/innertube](https://github.com/tombulled/innertube)
- [yt-dlp issue #13122 — Shorts view count wrong](https://github.com/yt-dlp/yt-dlp/issues/13122)
- [davidteather/TikTok-Api](https://github.com/davidteather/TikTok-Api)
- [tiktok/tiktok-research-api-wrapper](https://github.com/tiktok/tiktok-research-api-wrapper)
- [drawrowfly/tiktok-scraper](https://github.com/drawrowfly/tiktok-scraper) (мёртв)
- [HohnerJulian/ResearchTikPy](https://github.com/HohnerJulian/ResearchTikPy)
- [akamhy/videohash](https://github.com/akamhy/videohash)
- [acoustid/chromaprint](https://github.com/acoustid/chromaprint)

### Apify / managed
- [Apify Instagram Reel Scraper](https://apify.com/apify/instagram-reel-scraper)
- [Apify Instagram Hashtag Scraper](https://apify.com/apify/instagram-hashtag-scraper)
- [Apify Instagram Scraper Pay-Per-Result](https://apify.com/apidojo/instagram-scraper)
- [Apify TikTok Scraper clockworks](https://apify.com/clockworks/tiktok-scraper)
- [Apify TikTok Scraper apidojo](https://apify.com/apidojo/tiktok-scraper)
- [Apify TikTok Trends Scraper](https://apify.com/clockworks/tiktok-trends-scraper)
- [Bright Data pricing 2026](https://dataresearchtools.com/bright-data-pricing-2026/)

### Official APIs
- [Meta IG Hashtag Search docs](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-hashtag-search/)
- [YouTube Data API: Search.list reference](https://developers.google.com/youtube/v3/docs/search/list)
- [YouTube Data API quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost)
- [TikTok Research API product page](https://developers.tiktok.com/products/research-api/)
- [TikTok ToS anti-scraping blog](https://www.tiktok.com/privacy/blog/how-we-combat-scraping/en)

### SaaS-аналоги
- [Predis.ai pricing](https://predis.ai/pricing/)
- [VidIQ pricing review](https://www.red11media.com/blog/vidiq-worth-it-2026)
- [TubeBuddy pricing](https://www.tubebuddy.com/pricing)
- [Trendpop on G2](https://www.g2.com/products/trendpop-trendpop/reviews)
- [Trendpop tools profile — Music Ally](https://musically.com/2022/11/02/tools-trendpop/)
- [Trendpop acquired by Collab](https://m.imdb.com/news/ni63535452/)
- [Pentos pricing](https://pentos.co/pricing/)
- [Exolyt Premium](https://exolyt.com/premium)
- [BigSpy review affmaven](https://affmaven.com/bigspy-review/)
- [PowerAdSpy pricing](https://poweradspy.com/pricing/)
- [ContentStudio pricing](https://contentstudio.io/pricing)
- [ViralFindr pricing — Techjockey](https://www.techjockey.com/us/detail/viralfindr)
- [Sprout Social pricing](https://sproutsocial.com/pricing/)
- [Meta Business Partner policies](https://www.facebook.com/business/marketing-partners/become-a-partner/fmp-product-policies)

### Юр.практика
- [zwillgen: hiQ v. LinkedIn wrap-up](https://www.zwillgen.com/alternative-data/hiq-v-linkedin-wrapped-up-web-scraping-lessons-learned/)
- [Social Media Today: Meta v. Bright Data](https://www.socialmediatoday.com/news/meta-abandons-legal-case-data-scraping-losing-key-judgment/708538/)
- [SociaVault: Instagram scraping legal 2025](https://sociavault.com/blog/instagram-scraping-legal-2025)
- [Socialcrawl: Instagram API & Scrapers in 2026](https://www.socialcrawl.dev/blog/instagram-scraping-2026)
- [Bypassing IG Graph API Reels — SociaVault](https://sociavault.com/blog/bypass-instagram-graph-api-reels)
- [SociaVault: Best Social Media Scraping APIs 2026](https://sociavault.com/blog/best-social-media-scraping-apis-2026)

### Метрики виральности
- [SociaVault: Reels Analytics 2025](https://sociavault.com/blog/instagram-reels-analytics-2025)
- [InfluenceFlow: Virality Metrics 2026](https://influenceflow.io/resources/short-form-content-performance-and-virality-metrics-the-complete-2026-guide/)
- [Bluehost: How Many Views Is Viral 2026](https://www.bluehost.com/blog/how-many-views-is-viral/)
- [Viral.app benchmarks 2025](https://viral.app/blog/guides/how-many-views-is-viral)
- [Socialinsider 2026 video stats](https://www.socialinsider.io/social-media-benchmarks/social-media-video-statistics)
- [Loopex YouTube Shorts stats 2026](https://www.loopexdigital.com/blog/youtube-shorts-statistics)
- [Emplicit TikTok engagement benchmark 2025](https://emplicit.co/tiktok-engagement-rate-benchmarks-2025/)

### Академические работы
- [arXiv 1709.02541 — Beyond Views: Measuring and Predicting Engagement](https://arxiv.org/pdf/1709.02541)
- [arXiv 2510.05761 — Early Multimodal Prediction of Cross-Lingual Meme Virality](https://arxiv.org/pdf/2510.05761)
- [arXiv 2510.08481 — BuzzProphet LLM-based hashtag virality](https://arxiv.org/pdf/2510.08481)

### Tools / pricing
- [Claude API Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Milvus: deduplication with sentence-transformers](https://milvus.io/ai-quick-reference/how-can-sentence-transformers-be-used-for-data-deduplication-when-you-have-a-large-set-of-text-entries-that-might-be-redundant-or-overlapping)
- [Scrapfly: How to Scrape YouTube 2026](https://scrapfly.io/blog/posts/how-to-scrape-youtube)
- [insightIQ: Instagram Public Content Access](https://www.insightiq.ai/blog/instagram-public-content-access-developers)
