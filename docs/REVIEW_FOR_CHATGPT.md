# Code Review Request — maksim-bot, сессия 10 июня 2026

> Для ChatGPT-5. Ревью двух фич, добавленных за сессию: **(A) автопостинг
> Instagram-карусели** и **(B) Pipeline 3 «Селфи + B-roll»** (выбор по
> категориям + AI-генерация графики через Remotion). Прошу: найти баги
> корректности/конкуренции, оценить 3 архитектурных решения (в конце), и
> покритиковать там, где у нас слабо. НЕ нужно хвалить то, что уже доказано
> живьём (список ниже) — это проверено сильнее, чем статический анализ.

---

## 0. Контекст системы

`maksim-bot` — Telegram контент-бот для клиента (Максим, Life Drive: картинг +
глэмпинг). Форк личного бота. Прод: один сервер `nox-maksim` (Hetzner Ubuntu
24.04), systemd-сервис, python-telegram-bot v21, монолит `bot.py` (~21k строк)
+ модули `selfie/`, `carousel/`, `crosspost.py`, `video_assembler.py`,
`auto_broll.py` (Remotion), `hyperframes_broll.py` (HyperFrames).

**Важно про LLM-доступ:** генерация графики (Remotion) и часть пайплайнов
ходят в Claude через **Claude Code CLI на сервере** под ОДНИМ
`CLAUDE_CODE_OAUTH_TOKEN` (Max-подписка). Этот же токен шарится между:
maksim-bot (несколько пайплайнов) + `auto_broll`/`hyperframes` + deep-research
+ Cursor разработчика. Автофолбэка на платный API при soft-throttle НЕТ.
Это фон для Вопроса №2.

---

## 1. Что УЖЕ доказано живьём (не нужно перепроверять логикой)

- **IG-карусель опубликована реально**: пост из 7 слайдов ушёл в @yumsunov86
  (media_id получен, Graph API принял JPEG end-to-end). Гейт «IG принимает
  наши слайды» — снят.
- **Remotion-генерация работает на сервере**: standalone-прогон выдал 6 mp4
  за 337с, $0.44 по подписке; E2E через бота — 6 сцен за ~468с.
- **Telethon E2E зелёные**: карусель-рендер + IG-кнопка (sc.45/46), B-roll по
  категориям фото+клипы (sc.47), селфи→Remotion-графика (sc.48).
- **Unit-тесты**: IG-карусель 13, генератор описаний 6, B-roll категории 15.
- **Адверсариальное саб-ревью** уже прошло: P0/P1 нет; закрыта утечка /tmp в
  генерации + защитный лог потери слайдов.

Что НЕ проверено (закроет ручной тест клиента): визуальное качество
**финального смонтированного селфи-ролика С Remotion-B-roll** (E2E
останавливался до шага сборки).

---

## 2. Фича A — автопостинг Instagram-карусели

**Поток:** карточка → карусель рендерится в 6-7 PNG (1080×1350, HTML) → кнопка
«📲 Опубликовать карусель в IG» → берём персистнутые PNG проекта →
конвертируем в JPEG (IG требует JPEG) → выкладываем на nginx-media (публичные
URL) → Graph API: child-контейнер на слайд (`is_carousel_item`) → parent
`media_type=CAROUSEL` → poll → `media_publish`. Переиспользует токен/ig_user_id
и паттерн из рабочего `instagram_upload_reel`.

### crosspost.py — оркестрация Graph API + JPEG-конвертация

```python
def convert_pngs_to_jpegs(png_paths, out_dir, quality: int = 90) -> list:
    """PNG-слайды → JPEG (RGB, белый фон под альфой). IG требует JPEG."""
    from PIL import Image
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    jpegs: list = []
    for src in png_paths:
        src_p = Path(src)
        if not src_p.exists():
            continue
        try:
            im = Image.open(str(src_p))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1]); im = bg
            else:
                im = im.convert("RGB")
            dest = out / (src_p.stem + ".jpg")
            im.save(str(dest), "JPEG", quality=quality)
            jpegs.append(dest)
        except Exception as e:
            logger.warning(f"[carousel] PNG→JPEG failed for {src_p.name}: {e}")
    return jpegs


def instagram_upload_carousel(image_urls: list, caption: str = "") -> dict | None:
    """IG-карусель (2-10 JPEG-URL) через Graph API. Зеркало instagram_upload_reel."""
    access_token = _get_instagram_access_token()
    ig_user_id = _get_instagram_user_id()
    if not access_token or not ig_user_id:
        logger.error("Instagram not authorized."); return None
    urls = [u for u in (image_urls or []) if u]
    if not (2 <= len(urls) <= 10):
        logger.error(f"Instagram carousel needs 2-10 images, got {len(urls)}"); return None

    # Step 1: child-контейнеры
    child_ids: list = []
    for idx, url in enumerate(urls):
        resp = requests.post(
            f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
            data={"image_url": url, "is_carousel_item": "true", "access_token": access_token},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"IG carousel child {idx} failed: {resp.status_code} {resp.text[:300]}")
            return None
        cid = resp.json().get("id")
        if not cid:
            logger.error(f"IG carousel child {idx}: no container id"); return None
        child_ids.append(cid)

    # Step 2: parent CAROUSEL
    parent_resp = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
        data={"media_type": "CAROUSEL", "children": ",".join(child_ids),
              "caption": (caption or "")[:2200], "access_token": access_token},
        timeout=30,
    )
    if parent_resp.status_code != 200:
        logger.error(f"IG carousel parent failed: {parent_resp.status_code} {parent_resp.text[:300]}")
        return None
    parent_id = parent_resp.json().get("id")
    if not parent_id:
        logger.error("IG carousel parent: no container id"); return None

    # Step 3: defensive-poll (фото обычно готовы сразу; не FINISHED → всё равно пробуем publish)
    for _ in range(12):  # ~1 мин
        status_resp = requests.get(
            f"https://graph.facebook.com/v21.0/{parent_id}",
            params={"fields": "status_code,status", "access_token": access_token}, timeout=15,
        )
        if status_resp.status_code == 200:
            st = status_resp.json().get("status_code")
            if st == "FINISHED": break
            if st == "ERROR":
                logger.error(f"IG carousel parent ERROR: {status_resp.json().get('status','')}")
                return None
        time.sleep(5)

    # Step 4: publish
    publish_resp = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
        data={"creation_id": parent_id, "access_token": access_token}, timeout=30,
    )
    if publish_resp.status_code != 200:
        logger.error(f"IG carousel publish failed: {publish_resp.status_code} {publish_resp.text[:300]}")
        return None
    return {"id": publish_resp.json().get("id"), "platform": "instagram"}
```

### carousel/handlers.py — handler публикации (сокращённо)

```python
async def publish_carousel_to_instagram(update, context, chat_id=None) -> None:
    uid = _user_id_from_update(update)
    if chat_id is None: chat_id = update.effective_chat.id
    import bot as _bot, crosspost as _crosspost
    from bot_state import project_dir as _project_dir_fn
    if not _crosspost.instagram_is_connected():
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Instagram не подключён…"); return
    draft = _load_carousel_draft(uid) or {}
    data = pending.get(uid) or {}
    seed_card_id = draft.get("seed_card_id") or draft.get("notion_page_id") or data.get("notion_page_id")
    png_paths = []
    if seed_card_id:
        proj = _project_dir_fn({"notion_page_id": seed_card_id})
        cdir = (Path(proj) / "carousel") if proj else None
        if cdir and cdir.exists():
            png_paths = sorted(str(p) for p in cdir.glob("slide_*.png"))
    if len(png_paths) < 2:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Не нашёл слайды…"); return
    brand = _bot._get_active_brand_name()
    status = await context.bot.send_message(chat_id=chat_id, text=f"📲 Публикую {len(png_paths)} слайдов…")
    # Caption: выбранное описание карточки, иначе авто-генерация из seed-текста
    caption = (data.get("description") or "").strip()
    if not caption:
        seed = data.get("carousel_seed") or {}
        src_text = seed.get("text") or draft.get("theme") or ""
        if src_text:
            try:
                variants, _cta = await asyncio.to_thread(_bot._compose_publication_descriptions, src_text)
                caption = (variants[0].strip() if variants else "")
            except Exception as e:
                logger.warning(f"[carousel/ig] caption gen failed: {e}"); caption = ""
    out_dir = Path(tempfile.mkdtemp(prefix="carousel_jpg_"))
    try:
        jpegs = _crosspost.convert_pngs_to_jpegs(png_paths, out_dir, quality=90)
        if len(jpegs) != len(png_paths):
            logger.warning(f"[carousel/ig] JPEG: {len(jpegs)}/{len(png_paths)} слайдов")
        if len(jpegs) < 2:
            await status.edit_text("❌ Не удалось подготовить JPEG-слайды (нужно ≥2)."); return
        jpeg_urls = _publish_carousel_pngs_to_media([str(j) for j in jpegs],
                        card_id_short=(seed_card_id or "adhoc")[:20], brand=brand)
        if len(jpeg_urls) < 2:
            await status.edit_text("❌ Не удалось выложить слайды на хостинг."); return
        result = await asyncio.to_thread(_crosspost.instagram_upload_carousel, jpeg_urls, caption)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    # … status.edit_text успех/ошибка по result …
```

**Осознанные компромиссы (не нужно их «находить»):**
- Если часть PNG не сконвертилась — публикуем оставшиеся (≥2), логируем warning.
  Маловероятно (PNG свеже-отрендерены ботом).
- Caption: `data['description']` > авто-генерация из seed-текста. Хэштегов нет
  (сознательно). CTA направляет в «Telegram-канал, ссылка в шапке профиля»
  (хэндл Telegram на IG не кликабелен).

---

## 3. Фича B — Pipeline 3 «Селфи + B-roll»

Селфи-часть (запись → Whisper → правка текста → субтитры) уже была. Добавили
к B-roll picker'у: **(B1)** выбор библиотеки по категориям (пустые скрыты;
фикс — фото брались из обложечной папки-портретов, теперь из
`broll-library/photos/<brand>/<cat>`); **(B2)** «🎨 Сгенерировать графику (AI)»
→ Remotion (`auto_broll.generate_auto_broll(transcript, dir)`) → 6 сцен ИЗ
ТЕКСТА → клипы кладутся как обычные video-B-roll items → существующий
`assemble_auto_montage`.

### selfie/handlers.py — handler AI-генерации (Phase 2, самый тяжёлый)

```python
if action == "gen":
    transcript = (data.get("selfie_edited") or data.get("selfie_transcript") or "").strip()
    items = _items_from_pending(data)
    free = selfie_broll.MAX_BROLL_ITEMS - len(items)   # MAX = 7
    chat_id = query.message.chat_id
    if not transcript:
        await query.edit_message_text("⚠️ Нет текста для генерации графики."); return True
    if free <= 0:
        await query.edit_message_text(f"Достигнут лимит {selfie_broll.MAX_BROLL_ITEMS}…",
            reply_markup=selfie_broll.build_picker_keyboard(items)); return True
    await query.edit_message_text("🎨 Генерю графику (Remotion)… ~3-7 мин, дождись.")
    clips = []
    gen_dir = Path(tempfile.mkdtemp(prefix=f"selfie_gen_{user_id}_"))
    data.setdefault("selfie_gen_dirs", []).append(str(gen_dir))   # очистим после сборки/cancel
    _SAVE_PENDING(_PENDING)
    try:
        import auto_broll
        clips, _cost = await asyncio.to_thread(auto_broll.generate_auto_broll, transcript, gen_dir)
    except Exception as e:
        _LOGGER.error(f"[selfie/gen] auto_broll failed: {e}", exc_info=True); clips = []
    if not clips:
        items = _items_from_pending(data)
        await context.bot.send_message(chat_id=chat_id,
            text="❌ Не удалось сгенерировать графику…\n\n" + selfie_broll.build_picker_message(items),
            reply_markup=selfie_broll.build_picker_keyboard(items)); return True
    added = 0
    for clip in clips[:free]:
        _store_item(data, selfie_broll.BrollItem(kind="video", source=Path(clip), label=f"[AI] {Path(clip).stem}"))
        added += 1
    _SAVE_PENDING(_PENDING)
    # … send picker с «добавил N AI-сцен» …
    return True
```

`auto_broll.generate_auto_broll(script_text, out_dir)` (существующий, не наш):
синхронная, держит свой `_GEN_LOCK` (один прогон за раз, общий `AutoBroll.tsx`),
зовёт `claude -p` (Claude Code CLI) для генерации React/Remotion-сцен +
`npx remotion render`. Таймауты 720с Claude / 360с рендер, 2 fix-round.
Вызываем через `asyncio.to_thread` (не блокирует event loop). Сгенерированные
клипы — обычные video-items → `prepare_broll_in_project` копирует в project_dir
→ `assemble_auto_montage(layout="smart", broll_mode="real")`.

**B1 (категории)** — pure-функции `scan_library(kind, category)`,
`list_library_categories(kind)` (прячет пустые), `lookup_library_path(kind,id)`
(id = md5 от пути относительно корня → round-trip через callback_data, lookup
без знания категории). Покрыто 15 unit-тестами.

---

## 4. ВОПРОСЫ К РЕВЬЮЕРУ (главное — критика этих решений)

### Q1. UX долгой синхронной генерации (Remotion ~5-8 мин)
Сейчас: юзер жмёт «Сгенерировать графику», бот пишет «дождись ~3-7 мин»,
генерация идёт в `asyncio.to_thread`, по готовности шлёт результат. Плюсы:
просто, состояние в pending. Минусы: юзер «висит» 5-8 мин; если в это время
рестарт сервиса (deploy/OOM) — прогресс теряется.
**Спрашиваем:** стоит ли делать фоновую джобу с устойчивостью к рестарту
(очередь + persisted job state), или для одного клиента это overkill и хватит
текущего синхронного подхода? Где грань.

### Q2. Шеринг одного OAuth-токена подписки между workload'ами
Один `CLAUDE_CODE_OAUTH_TOKEN` (Max-подписка) обслуживает: Remotion-генерацию
в селфи + `auto_broll`/`hyperframes` в Pipeline 4 + deep-research + Cursor.
Автофолбэка на платный API при throttle нет. `auto_broll` держит свой
module-level `_GEN_LOCK` (сериализует ВНУТРИ процесса бота), но не
координируется с другими процессами на том же токене.
**Спрашиваем:** какой минимальный механизм координации оправдан? (межпроцессный
lockfile / семафор; алертинг на throttle; force-fallback на платный API). Или
принять как риск, раз клиент один и нагрузка низкая? Что бы сделал ты.

### Q3. Интеграция сгенерированных клипов как «обычных» B-roll items
Сгенерированные 6 сцен кладём в общий список B-roll items (лимит 7), дальше тот
же `assemble_auto_montage(broll_mode="real")`, что и для библиотечных клипов.
Плюс: единый монтажный путь, можно мешать AI-графику + библиотеку. Альтернатива
была: генерить в `autobroll/` и собирать `broll_mode="ai"` отдельным путём.
**Спрашиваем:** ок ли смешивать AI-графику и «живые» библиотечные клипы в одном
`smart`-монтаже (видео — на полную длину поверх селфи)? Не получится ли каша, и
не лучше ли AI-графику делать отдельным режимом ролика? (Финальный смонтированный
результат мы ещё НЕ смотрели глазами — это наш следующий ручной тест.)

### Q4 (опц.). Корректность Graph API / edge-cases
Любые баги в `instagram_upload_carousel` (порядок children, обработка
ERROR-статуса, отсутствие cleanup orphan-контейнеров при частичном провале),
в `convert_pngs_to_jpegs`, в `publish_carousel_to_instagram` (resolve слайдов
по seed_card_id, потеря слайда при сбое конвертации).

---

## 5. Что хотим на выходе
Приоритизированный список: **(1)** реальные баги корректности/конкуренции;
**(2)** твой вердикт по Q1-Q3 с обоснованием «почему так для одного клиента с
низкой нагрузкой»; **(3)** что бы ты сделал иначе архитектурно, если это дёшево.
Без воды и без похвалы проверенного.
