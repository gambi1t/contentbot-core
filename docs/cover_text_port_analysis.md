---
name: cover-text-port-analysis
description: CTO-анализ перед портом текста-на-обложке + Notion-сохранения из content-bot-2/maksim-bot в селфи-пайплайн. Главный вывод — generate_cover (PIL) готов к порту; Notion = EXTERNAL URL (не file-upload, вопреки ожиданию Артёма).
metadata:
  type: research
  date: 2026-06-09
  branch: maksim-bot
---

# Порт текста-на-обложке + Notion: CTO-анализ

## TL;DR

1. **Текст на обложке** — функция `generate_cover` (PIL/Pillow) идентична в обоих ботах, готова к порту. Адаптивный шрифт + умная полупрозрачная подложка под текст (подбирает цвет под фон) + градиент затемнения. ~22 мин на порт в селфи.
2. **Notion-сохранение** — ⚠️ **Артём, тут поправлю: фото сохраняется НЕ «внутри» Notion, а EXTERNAL-ссылкой** на nginx (`/covers/`). Визуально картинка рендерится в карточке (выглядит «внутри»), но под капотом это ссылка. Доказательство: `create_notion_card` оба бота используют `"type": "external"`. Мой вчерашний фикс cover→Notion уже работает так же (канонично). Truly-inside (file-upload байтами) — отдельная, бо́льшая задача, если нужна вечная durability.

## 1. Текст на обложке — `generate_cover` (PIL)

**Где:** content-bot-2 `bot.py:2638`, maksim-bot `bot.py:3572`. **Функции идентичны** (копия).

**Механика:**
- Canvas 1440×2560 (9:16), JPEG q=97.
- **Шрифт:** Montserrat-SemiBold (есть в `maksim-bot/assets/fonts/`; content-bot-2 не имеет → падает на Arial Bold).
- **Адаптивный размер** по длине хука:
  - ≤15 симв / ≤3 слова → 175px
  - ≤25 симв / ≤5 слов → 140px
  - иначе → 110px
  - + авто-downsize по 5px пока не влезет.
- **Перенос:** `textwrap.wrap` по словам.
- **Подложка под текст («pill»)** — ключевая фишка:
  - закруглённый прямоугольник (radius 42), на 72% высоты вниз (грудь/живот),
  - **цвет адаптивный**: сэмплит пиксели фото за подложкой → blend 60% к белому → светлый полупрозрачный под фон,
  - alpha 210 (видно фото сквозь).
- **Текст:** почти чёрный (25,25,25), по центру.
- **Градиент** затемнения снизу (45%→низ) для контраста.

**Вход:** `generate_cover(cover_text, output_path, avatar_override)`. Бренд-параметров нет.

## 2. Notion-сохранение — РЕАЛЬНОСТЬ vs ожидание

**Артём думал:** «фото лежат прямо внутри Notion, не ссылкой».
**В коде:** оба бота — **external URL**.
- maksim-bot `create_notion_card` (bot.py:3752): image-блок `"type": "external", "external": {"url": cover_url}` (3788), page-cover баннер тоже external (3883).
- content-bot-2 `create_notion_card` (bot.py:2814): то же (2838).
- Источник URL — `save_media_permanent` → nginx `/covers/`.
- **File Upload API (`/v1/file_uploads`, байты внутрь Notion) НЕ используется** — оба бота старше этого API.

**Почему выглядит «внутри»:** Notion рендерит external-image инлайн (видишь картинку, не ссылку). Поэтому ощущение «внутри» верное визуально, но технически это ссылка.

**Последствие:** если nginx-файл удалить — картинка в Notion сломается (external). File-upload был бы вечным. Для рабочего процесса (карточка живёт недолго до публикации) external достаточно.

**Мой вчерашний фикс cover→Notion** (`save_media_permanent` + `cover_url`) — **уже канонично**, совпадает с обоими ботами. То что вчера обложки не было в Notion — потому что `cover_url` не передавался, теперь передаётся.

## 3. План порта в селфи

**Точка 1.** `generate_cover` доступна в maksim-bot/bot.py:3572 (уже в том же файле, что `_selfie_finalize`). Импортировать в selfie не нужно — вызвать прямо в `_selfie_finalize`.

**Точка 2 — врезка в `_selfie_finalize` (bot.py:7005), после получения cover_path:**
```python
if cover_path and Path(cover_path).exists() and title:
    cover_with_text = str(selfie_tmp / "cover_with_text.jpg")
    generate_cover(title, cover_with_text, avatar_override=str(cover_path))
    cover_path = cover_with_text  # дальше идёт в Notion + проект
```
- `title` = выбранный хук (5 хуков Opus). На обложку ляжет тот же текст, что и заголовок карточки — логично.
- `avatar_override` = выбранное селфи-фото (кадр/библиотека/upload). КРИТИЧНО передать, иначе...

**⚠️ ГЛАВНЫЙ РИСК:** `generate_cover` при `avatar_override=None` берёт **случайный аватар** (`_pick_random_avatar`). Для селфи это неверно — нужна выбранная обложка. Поэтому ВСЕГДА передавать `avatar_override=cover_path`. Проверить, что generate_cover корректно работает с произвольным фото (не только аватаром Максима) как фоном.

**Точка 3 — Notion:** уже работает (cover_url через external). Изменений не нужно — обложка-с-текстом уйдёт тем же путём.

## Развилки для Артёма

1. **Текст на обложке** = title (выбранный хук) или отдельный короткий текст? Title может быть длинным (110px шрифт). Предлагаю: брать выбранный хук как есть (он короткий, 4-10 слов — под обложку идеально).
2. **Notion durability:** оставить external URL (работает, но ломается если nginx-файл удалить) ИЛИ вложить в file-upload API (вечно, но +SDK-проверка + рефактор)? Рекомендую external (как у тебя везде), file-upload — если будет реальная проблема с битыми картинками.
3. **Опциональность:** текст на обложку — всегда или кнопкой «с текстом / без»? (как монтаж).

## Оценка
Порт ~30-40 мин: вынести/вызвать generate_cover + adapt random-avatar guard + TDD на «avatar_override обязателен» + Telethon (обложка с текстом приходит). Низкий риск — функция проверена в проде у Артёма.

## Источники (file:line)
- maksim-bot/bot.py:3572 `generate_cover`
- content-bot-2/bot.py:2638 `generate_cover` (идентична)
- maksim-bot/bot.py:3752 / content-bot-2/bot.py:2814 `create_notion_card` (external image)
- maksim-bot/bot.py:7005 `_selfie_finalize` (точка врезки)
- selfie/handlers.py:1578 `_finalize_with_cover` (текущая обложка без текста)
