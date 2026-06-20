# Паритет legacy(content-bot) ↔ core — дыры перед cutover panferov

> Парити-аудит 2026-06-20 (run wf_9ecdc3fa-66e). Вердикт: **NO-GO-until-port.**
> Допущение «core = надмножество legacy» ОПРОВЕРГНУТО. Core новее (новые движки — НЕ откатывать), но потерял ~10 свежих прод-фиксов panferov (хотфиксы content-bot июнь 6-20 не доехали в maksim-prod).
> LEGACY = `/root/content-bot` (источник правды «что есть у Артёма сегодня»). CORE = `/root/contentbot-core` = `maksim-prod` (= `D:\AI\maksim-bot`).
> Правило порта: брать готовый код из legacy, адаптировать, **TDD-first** (легаси-замки уже есть: `test_subtitle_burn_params`, `test_selfie_intake`, `test_split_anchor`).

## 🔴 БЛОКЕРЫ (ломают рабочий пайплайн Артёма после свитча)

### B1. Selfie PATH B — приём selfie >20MB через «Избранное» (#selfie) — `complex-port`
- **Legacy:** `telethon_uploader.py` `SELFIE_TAG="#selfie"`/`_find_active_selfie`/пишет `selfie_source`+`selfie_video_ready`; `selfie/handlers.py` `BOT_API_DOWNLOAD_LIMIT_MB=20`/`_intake_keyboard` (кнопка «✅ Обработать видео»)/`handle_intake_callback`; `bot.py` диспетч `selfie_intake`.
- **Core:** загрузчик ловит только `#crosspost`/`#lib`; bot-side `_intake_keyboard`/`handle_intake_callback`/`selfie_intake` отсутствуют. Core входит в `selfie_waiting_video`, но для >20MB Bot API `getFile` падает → **бот ждёт вечно**.
- **Порт:** (a) `#selfie`-хендлер в `telethon_uploader.py`; (b) `BOT_API_DOWNLOAD_LIMIT_MB`/`_intake_keyboard`/`handle_intake_callback` + size-route в `process_video`; (c) диспетч `selfie_intake:` в `bot.py`; (d) текст `/selfie` с инструкцией. Восстановить `tests/test_selfie_intake.py`.

### B2. «Скачать материалы» в режиме редактирования — `trivial-copy`
- **Legacy** `bot.py:712`: `notion_id = notion_page_id OR notion_edit_card` (фикс 18 июня).
- **Core** `bot_state.py:41`: резолвит ТОЛЬКО `notion_page_id` → в edit-режиме (`pending['notion_edit_card']`, без `notion_page_id`) `project_dir→None` → отдаёт только `script.txt`+обложку, БЕЗ файлов/фото. Бьёт по массовому flow shoes/default.
- **Порт:** fallback в `bot_state.project_dir`: `notion_id = notion_page_id or notion_edit_card`; `title = card_data.title or notion_edit_title`. + тест edit-mode.

## 🟠 МЕЙДЖОРЫ

| # | Фича | Суть | Сложность |
|---|---|---|---|
| M1 | Burn-пресет субтитров | core `medium/crf15/timeout=600` — те параметры, от которых ушёл фикс 18 июня (минутный ролик падал по timeout). Нужно `veryfast/crf20/timeout=900` + `test_subtitle_burn_params`. Core пофиксил ДРУГОЙ timeout (download), не burn. Бьёт по КАЖДОМУ ролику с субтитрами. | trivial |
| M2 | Brand-словари субтитров + Whisper-промпт | core урезан/переписан под Максима (картинг/глэмпинг); AI-лексикон Артёма (~25: Cursor/Opus/Claude/GPT-5/Grok…) выброшен → субтитры «Кьюрсор/Опус», падает точность. | moderate (per-tenant) |
| M3 | Floating PiP аватар (🫧) | формат 8 июня: кнопка + layout `o`/`floating` + `PIP_DIAMETER`/`_plan_floating_montage` + pip-сегмент. В core нет ничего. | complex |
| M4 | Shoes split-anchor = 1.0 | legacy `SHOES_SPLIT_ANCHOR=1.0` (0.75/0.62 срезали обувь снизу, замок `test_split_anchor`). Core хардкод `0.75` (намеренно?) → режет продукт на обувном. | moderate (нужно подтв.) |
| M5 | `cover_prompt_shoes.txt` | внешний промпт обложки shoes (10988B, 6 июня) в core ОТСУТСТВУЕТ → откат качества обложек обувного. | trivial |
| M6 | `filters.Document.IMAGE` | core роутит только `filters.PHOTO` → аватар/материалы файлом (без сжатия) тихо проваливаются мимо `process_photo`. | trivial |
| M7 | `MAKSIM_MUSIC_DIR` | core дефолт `/srv/bot-music-maksim` (нет на хосте) → музыка selfie = 0 треков. Указать на музыку Артёма (env/tenant). | env |
| M8 | Рекурсивный сбор фото в fallback «Скачать материалы» | core fallback `iterdir()` нерекурсивный → теряет `photos/` на больших проектах (ZIP>48MB). Основной ZIP-путь рекурсивный (ок). | trivial |

## 🟡 МИНОРЫ
- Текст `/selfie` без инструкции PATH B (портируется с B1).
- Look «Жёлтая рубашка» (shoes heygen_looks: core только `main`).
- pro→smart/fullscreen авто-роут для фото-only (core решил иначе, не падает).
- `_project_broll_inventory` (фото+видео в «Управление B-roll» на фото-only) — НЕ подтверждено.

## ✅ ЭКВИВАЛЕНТНО / core впереди (НЕ трогать)
selfie/edit.py (идентично), selfie/cover.py (core надмножество), selfie/music.py (логика идентична), Full-screen в меню (есть), Ken Burns shoes, HeyGen /v3+motion_prompt (портирован), crosspost TG/IG/YT/VK/TikTok (core надмножество), brand switch fix, 413/large-final delivery (core впереди), основной ZIP «Скачать материалы» (рекурсивный, ок).

## Рекомендованный порядок порта (TDD-first)
1. **B2** _project_dir fallback (тривиально, замок) — чинит «Скачать материалы» edit-flow.
2. **M1** burn-пресет veryfast/crf20/timeout=900 + восстановить `test_subtitle_burn_params`.
3. **M8** рекурсивный fallback фото.
4. **B1** Selfie PATH B E2E (отдельным заходом, complex) + `test_selfie_intake`.
5. **M2** brand-словари per-tenant (AI-лексикон Артёма, не затирая Максима).
6. **M6** Document.IMAGE.
7. **M5** cover_prompt_shoes.txt.
8. **M7** MAKSIM_MUSIC_DIR per-tenant.
9. **M4** shoes anchor 1.0 (после подтверждения Артёма).
10. **M3** floating PiP (если используется).
11. Миноры по остаточному принципу.

## Вопросы Артёму (меняют severity/scope)
- B1 selfie PATH B — регулярно шлёшь большие селфи через Избранное? (блокер vs major)
- M3 floating PiP — продакшен или эксперимент?
- M4 shoes anchor — всё ещё smart-mix на обувном? 1.0 vs 0.75 (нужен твой кадр)?
- M2 brand-словари — per-tenant раздельные (реком.) или общие?
- M5/yellow_shirt — дропнуты намеренно (scope maksim) или случайно?
- M7 music / cover-library dir для panferov — на ассеты Артёма или Максима?
- PATH A (короткий selfie напрямую) — подтвердить рантайм цел (аудит read-only не гонял).

## Процессный вывод (root cause)
Хотфиксы Артёма (content-bot, июнь 6-20) шли в `panferov-legacy` и НЕ портировались в `maksim-prod`/core — накопился бэклог непортированных фиксов. Это и сломало посылку «core ⊇ legacy». Впредь: хотфикс → немедленный порт в core (trunk-discipline).
