# Carousel package review — Claude → внешний reviewer (ChatGPT/Codex)

**Контекст:** maksim-bot, форк @panferovai_contentbot под клиента
Максима Юмсунова (Life Drive — картинг + глэмпинг, Тюмень). Прод бежит на
`nox-maksim` (Hetzner 89.167.89.133), под user `maksim-bot`, systemd-service.

**Что ревьюим:** пакет правок «Карусель для Instagram» (Pipeline #6),
сделанный 26 мая 2026 в три волны:

1. **P0+P1+P2** — base fixes: точка входа из карточки, replace-by-substring в
   точечной правке, persistent draft, no-op detect, грамматика в промпте,
   логи diff.
2. **TOP-retry + back-to-preview** — авто-повтор при TOP-poisoned cover +
   кнопка «✏️ Поправить ещё» после рендера PNG.
3. **A+B4+C** — PNG в проект карточки, submenu смены статуса, защита от
   перезаписи draft.

Покрытие тестами: 17 unit-тестов GREEN
(`tests/test_carousel_surgical_helpers.py`), 6 Telethon-сценариев GREEN
(`contentbot-tests/scenarios/20-25_*.yaml`). НЕ покрыто Telethon'ом:
реальные Opus/Sonnet вызовы (LLM-логика, no-op детект, TOP-retry, статус
update в Notion).

---

## Pipeline overview — как работает карусель

### Точки входа
1. **Главное меню → «🎨 Карусель для Instagram»** (старый flow, остался):
   ```
   cmd_carousel callback → выбор шаблона M1/M2 → state="awaiting_carousel_theme"
   → юзер шлёт тему текстом/голосом → generate_carousel_preview(theme=...)
   ```
2. **Меню карточки → «🎨 В карусель»** (НОВЫЙ flow, P0):
   ```
   card_to_carousel:<id> → достаёт script из Notion → pending["carousel_seed_text"]
   → выбор шаблона M1/M2 → carousel_tpl:<X> видит seed → generate_carousel_preview
   с seed как темой, БЕЗ awaiting_carousel_theme
   ```

### Phase 1 — preview
`carousel.handlers.generate_carousel_preview(theme, ..., seed_card_id=None)`:
- Зовёт `carousel.llm.generate_carousel(theme, n_slides, model="claude-opus-4-7", template)`.
- Сохраняет draft в `bot.pending[uid]["carousel_draft"]` (теперь **persistent JSON**,
  раньше — `context.user_data` / RAM-only).
- Шлёт текстовое preview + кнопки `[✅ Делаем PNG] [✏️ Точечная правка] [🔄 Переписать
  полностью] [📋 К карточке] [❌ Отмена]`.

### Phase 1.5 — точечная правка (loop)
- Юзер жмёт «✏️ Точечная правка» → state="awaiting_carousel_surg_edit" → ждёт
  инструкцию текстом/голосом.
- text/voice handler в `bot.py` зовёт `apply_carousel_surgical_edit(instruction)`.
- Sonnet 4.6 (`carousel.llm.surgical_edit_carousel`) меняет ТОЛЬКО просимое,
  возвращает edited JSON.
- **NEW (P0)**: если `_slides_equal_normalized(slides, new_slides) == True` —
  no-op → сообщение «правка не применена, переформулируй» + сохранение counter.

### Phase 2 — render PNG
`carousel.handlers.render_carousel_from_draft`:
- Берёт draft из pending.
- B-roll фоны через `carousel.broll.pick_background_photos`.
- Renderer (Playwright) → PNG в `/tmp/carousel_<chat_id>_<rand>/slide_NN.png`.
- `send_media_group` → юзер видит карусель.
- **NEW (A)**: если в draft есть `seed_card_id` → `_persist_carousel_pngs` копирует
  PNG в `projects/<card_id>/carousel/` (попадают в zip-архив, переживают рестарт).
- **NEW (B4)**: если `seed_card_id` есть → в финальное меню добавляется
  «📊 Сменить статус карточки ▼».
- **NEW** (раньше): draft НЕ удаляется после рендера — оставляется чтобы юзер
  мог «✏️ Поправить ещё» → возврат к preview без потери работы.

---

## Изменённые / добавленные файлы

- `bot.py` — handler'ы callback'ов, точка входа из карточки.
- `carousel/handlers.py` — orchestration + persistent draft + persist PNG.
- `carousel/llm.py` — promptы Opus/Sonnet + хелперы equality/replace.
- `tests/test_carousel_surgical_helpers.py` — 17 unit-тестов.
- `contentbot-tests/scenarios/25_maksim_card_to_carousel.yaml` — Telethon.

---

## 6 фокусных вопросов для review

### Q1. Late-import `bot.pending` из `carousel/handlers.py` — circular dep risk?

`carousel/handlers.py` импортирует `bot` модуль на каждом обращении к draft:

```python
def _pending_io():
    import bot as _bot
    return _bot.pending, _bot._save_pending


def _load_carousel_draft(user_id: int) -> dict | None:
    pending, _ = _pending_io()
    return (pending.get(user_id) or {}).get("carousel_draft")


def _save_carousel_draft(user_id: int, draft: dict) -> None:
    pending, save_fn = _pending_io()
    pending.setdefault(user_id, {})["carousel_draft"] = draft
    save_fn(pending)


def _drop_carousel_draft(user_id: int) -> None:
    pending, save_fn = _pending_io()
    p = pending.get(user_id)
    if p and "carousel_draft" in p:
        del p["carousel_draft"]
        save_fn(pending)
```

`bot.py` импортирует `carousel.handlers` на топ-уровне (для регистрации
handler'ов). `carousel.handlers` НЕ импортирует `bot` на топ-уровне (только
внутри функций) — это намеренно для избежания цикла.

**Вопросы:**
- Достаточно ли это для безопасности? Не сломается ли при добавлении нового
  глобала в `bot.py` который тоже импортит `carousel.handlers`?
- `_bot.pending` мутируется в обоих местах (bot.py и carousel/handlers.py
  параллельно). PTB single-threaded async, но `_save_pending` пишет на диск —
  есть ли risk race condition при двух одновременных callback'ах?
- `_save_pending(pending)` целиком сохраняет весь dict — операция атомарна?
  (Использует ли write-then-rename pattern?) — если crash при записи, теряется
  ли всё или только часть.

### Q2. Поток `carousel_seed_*` через pending → draft

Когда юзер жмёт «🎨 В карусель» из карточки:

```python
# bot.py: card_to_carousel handler
pending[user_id]["carousel_seed_text"] = script_text.strip()
pending[user_id]["carousel_seed_card_id"] = full_id
pending[user_id]["carousel_seed_card_url"] = card.get("url", "")
```

Потом юзер кликает шаблон M1/M2 → `carousel_tpl:`:

```python
# bot.py: carousel_tpl handler
seed_text = data_local.pop("carousel_seed_text", None)
seed_url = data_local.pop("carousel_seed_card_url", None)
seed_card_id = data_local.pop("carousel_seed_card_id", None)
if seed_text:
    await generate_carousel_preview(
        update, context, claude,
        theme=seed_text,
        notion_url=seed_url,
        template=tpl,
        seed_card_id=seed_card_id,  # → попадёт в draft
    )
    return
```

`generate_carousel_preview` пишет draft:

```python
_save_carousel_draft(_user_id_from_update(update), {
    "slides": slides,
    "theme": theme,
    "n_slides": n_slides,
    "notion_url": notion_url,
    "chat_id": chat_id,
    "template": template,
    "seed_card_id": seed_card_id,
})
```

**Вопросы:**
- Что если юзер: открыл карточку → нажал «🎨 В карусель» → НЕ выбрал шаблон
  (закрыл диалог) → потом открыл другую карточку → «🎨 В карусель» → шаблон.
  В первом проходе `carousel_seed_text` лежит в pending, но pop не вызывался.
  Во втором проходе seed перезапишется на вторую карточку — это OK. Но если
  юзер потом вернётся к первой и нажмёт шаблон — seed уже второй карточки.
  Race-condition пограничный, но возможен.
- `data_local.pop` чистит seed_* поля ВСЕГДА, даже если juzер не пошёл в
  карусель — потенциальная очистка чужого state.
- В `regenerate_carousel_preview` (callback `carousel_regen`) сейчас передаёт
  `seed_card_id=draft.get("seed_card_id")` — это правильно для re-gen старой
  карточки, но если юзер делает regen на полностью новой теме — он остаётся
  привязан к старой карточке. Намеренно?

### Q3. TOP-poisoning retry — двойные деньги Opus?

```python
# carousel/llm.py: generate_carousel
slides = _one_call()           # call 1: $$
slides = _strip_top_word(slides)

if _cover_has_empty_critical_fields(slides[0]):
    try:
        retry_slides = _one_call()           # call 2: $$
        retry_slides = _strip_top_word(retry_slides)
        if not _cover_has_empty_critical_fields(retry_slides[0]):
            slides = retry_slides
        else:
            # Опять отравлено — fallback на безопасные плейсхолдеры
            cover = slides[0]
            if not (cover.get("title_main") or "").strip():
                cover["title_main"] = "ВЫВОДЫ"
            if not (cover.get("title_accent") or "").strip():
                cover["title_accent"] = "ДНЯ"
    except Exception as e:
        logger.error(f"[carousel-llm] retry failed: {e}", exc_info=True)
        cover = slides[0]
        if not (cover.get("title_main") or "").strip():
            cover["title_main"] = "ВЫВОДЫ"
        if not (cover.get("title_accent") or "").strip():
            cover["title_accent"] = "ДНЯ"
```

ПОВЕРХ этого есть уже существующий retry для `count-mismatch`:

```python
try:
    slides = _one_call()
except ValueError as e:
    if "slide count mismatch" in str(e):
        slides = _one_call()  # retry #1
    else:
        raise
```

**Вопросы:**
- В худшем случае: первый `_one_call()` → count mismatch → retry → success →
  но TOP-poisoned → retry опять → итого **3 вызова Opus** = ~$0.30-1.00 за
  одну генерацию. Это OK?
- TOP-poisoning detect срабатывает только когда **оба** title пусты после strip.
  А если Opus засунул ТОП только в title_main (один пустой, другой
  нормальный) — detect не сработает, юзер увидит частично кривое cover.
  Стоит ли расширить до «хоть одно пусто»?
- Fallback «ВЫВОДЫ / ДНЯ» — это generic. Может это сбивать с темы карусели?
  Лучше брать из темы / kicker?

### Q4. C-диалог fall-through через мутацию `query.data` — антипаттерн PTB?

```python
# bot.py
if query.data.startswith("card_to_carousel_force:"):
    # «🔄 Сделать заново» из C-диалога: дропаем старый draft и идём
    # обычным flow card_to_carousel.
    try:
        from carousel.handlers import _drop_carousel_draft
        _drop_carousel_draft(query.from_user.id)
    except Exception as e:
        logger.warning(f"[card_to_carousel_force] drop draft failed: {e}")
    # Подменяем callback и проваливаемся в обычный handler ниже.
    query.data = "card_to_carousel:" + query.data.split(":", 1)[1]
    # fall through

if query.data.startswith("card_to_carousel:"):
    # ... обычный flow
```

**Вопросы:**
- Мутация `query.data` — это валидный PTB-паттерн? Telegram сам так не делает,
  это **наша** подмена в локальном scope. Risk: если есть middleware / другой
  handler в цепи, который читает `query.data` ПОСЛЕ нашего блока, он увидит
  мутированное значение.
- Альтернатива — вынести логику `card_to_carousel:` в функцию, звать её из
  обоих веток. Стоит ли?
- В C-диалоге кнопка «✏️ Открыть существующий» → callback `carousel_back_to_preview`
  (уже существует). Сейчас этот callback **не проверяет**, что draft принадлежит
  именно той карточке которую юзер пытался открыть. Если у юзера draft от
  ДРУГОЙ карточки — он откроется (хотя `_existing_carousel_for_card_detect`
  до этого уже сравнил url'ы, так что в нормальном flow всё OK; но если
  draft за это время устарел и notion_url не совпадает — мы окажемся в чужом
  draft).

### Q5. `_SURG_EDIT_SYSTEM` промпт — внутренние противоречия?

Усилены два блока (выделено новое жирным):

```
🎯 REPLACE-ЗАПРОСЫ — самый частый тип инструкции. Формат:
  «<X> поменяй на <Y>», «замени <X> на <Y>», «<X> → <Y>».

ОБРАБОТКА REPLACE:
1. Найди подстроку <X> в любом поле любого слайда (title, kicker, body,
   title_main, title_accent, hero, hero_word, subtitle, pull_quote).
2. ⚠️ ПРИ ПОИСКЕ нормализуй пробелы: двойные/тройные пробелы в <X> или
   в поле слайда считай эквивалентом одинарного. Пример: <X> = «1 СОТРУДНИК
   КОТОРЫЙ» (с одним пробелом) ДОЛЖЕН совпасть с полем «1 СОТРУДНИК  КОТОРЫЙ»
   (с двумя пробелами).
3. Если нашёл — замени именно эту подстроку на <Y>, остальное сохрани.
4. Если <X> НЕ найдено даже после нормализации пробелов — добавь к
   первому слайду поле "_surg_error": "не нашёл: <X>" и верни остальное
   без изменений. Это сигнал пользователю переформулировать.
5. ОБЯЗАТЕЛЬНО: при успехе хоть ОДНО поле в JSON должно отличаться
   от исходного. Если ты возвращаешь идентичный JSON — это сигнал
   что ты не понял инструкцию (см. правило 4).
```

И существующее правило: «Задача — внести ТОЧЕЧНУЮ правку строго по инструкции,
сохранив всё остальное БАЙТ-В-БАЙТ.»

**Вопросы:**
- Правило 5 («хоть одно поле должно отличаться при успехе») vs правило 4
  («если не нашёл — верни без изменений»). Если Sonnet НЕ нашёл X и возвращает
  «без изменений» + _surg_error — формально это no-op = правило 5 нарушается?
  Sonnet может интерпретировать как «должен что-то изменить любой ценой» и
  выдумает изменение.
- Поле `_surg_error` в первом слайде — не сломает ли `_validate_slides`?
  (Оно проверяет required fields, но не запрещает дополнительные.)
- `apply_carousel_surgical_edit` сейчас НЕ читает `_surg_error` — только смотрит
  на `_slides_equal_normalized` и показывает generic-сообщение. Стоит ли
  прокинуть `_surg_error` дальше юзеру?

### Q6. No-op detect и TOP-poisoning detect — конфликтуют ли при surgical edit?

Surgical edit над уже сгенерированным cover (где title_main="ВЫВОДЫ" fallback):
- Юзер просит «cover_main замени на ОТЛИЧИЯ» → Sonnet меняет → no-op detect:
  `slides[0].title_main = "ВЫВОДЫ"` vs `new_slides[0].title_main = "ОТЛИЧИЯ"`
  → не равны → не no-op → OK, применяется.
- Юзер просит «убери слово ТОП с обложки» — но «ТОП» уже стрипнут при первой
  генерации, и в cover его нет. Sonnet возвращает без изменений → no-op detect
  срабатывает → юзер видит «правка не применена» — это правильное поведение
  («не нашёл что менять»).
- Sonnet возвращает поле `_surg_error: "не нашёл: ТОП"` (как промпт просит) →
  `_slides_equal_normalized` сравнит slides. Если у `new_slides[0]` появилось
  поле `_surg_error` которого не было в `slides[0]` — equal вернёт False
  (set(keys) разные)! No-op НЕ сработает → handler пойдёт по success-пути →
  draft.slides обновится с этим _surg_error → preview будет с мусорным полем.

**Вопросы:**
- Не нужно ли в `_slides_equal_normalized` игнорировать `_surg_error` поле?
  Или в `apply_carousel_surgical_edit` сначала чистить `_surg_error` перед
  сравнением?
- Если Sonnet вернул `_surg_error` — UX правильнее показать сообщение с
  конкретной причиной из этого поля, не generic «не применена».

---

## Полезные файлы

Тесты:
- `tests/test_carousel_surgical_helpers.py` — 17 unit-тестов (логика, без LLM).
- `contentbot-tests/scenarios/25_maksim_card_to_carousel.yaml` — Telethon.

Логика:
- `carousel/handlers.py:40-113` — persistent draft + persist PNG + draft-detect.
- `carousel/handlers.py:336-440` — `render_carousel_from_draft` (A + B4 интегрированы).
- `carousel/handlers.py:441-490` — `back_to_carousel_preview`, `finalize_carousel`.
- `carousel/handlers.py:536-650` — `apply_carousel_surgical_edit` (no-op detect).
- `carousel/llm.py:62-95` — `_SYSTEM_PROMPT` грамматика + правило не резать хвост.
- `carousel/llm.py:415-460` — `_SURG_EDIT_SYSTEM` промпт surgical edit.
- `carousel/llm.py:490-560` — хелперы `_slides_equal_normalized`,
  `_extract_replace_pattern`, `_cover_has_empty_critical_fields`.
- `bot.py: handler` block — `card_to_carousel:`, `card_to_carousel_force:`,
  `carousel_tpl:`, `carousel_status_menu`, `carousel_set_status:`,
  `carousel_back_to_preview`, `carousel_finalize`, `carousel_status_cancel`.

---

## Что ожидаем от review

- Ranked verdict по 6 пунктам: **Critical / Medium / Minor** для каждого.
- Конкретный фикс (файл:строка + 1-фразный совет) для Critical и Medium.
- Если найдёте что-то критическое **вне** 6 пунктов — отдельным блоком.

Telethon-сценарии можно расширить — если предложите конкретные кейсы
которые я могу проверить без LLM-вызова (т.е. UI/state-machine), укажите.
