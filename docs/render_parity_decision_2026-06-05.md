# Решение Step 4: render-движок production = свой puppeteer

**Дата**: 5 июня 2026
**Контекст**: Phase 1 Step 4 production-плана HyperFrames B-roll для maksim-bot.
**Решение принял**: Claude (CTO Артёма) на основании парити-теста.

## TL;DR

Production-render остаётся **наш `tools/render_scene.mjs`** (puppeteer + ffmpeg),
а **не** `npx hyperframes@0.6.56 render`.

Аргумент: парити на 3 сценах = **67% совместимости** — неприемлемо для prod.
scene_01 (3D card-flip) в npx даёт **чёрный экран**.

## Парити-тест (4 + 5 июня)

Прогон 3 сцен из доказанного `D:\AI\hf_local_diag\` на обоих движках:

| Сцена | Архетип | Наш render | npx render |
|---|---|---|---|
| scene_01 | before_after_cards · css_3d_transforms | ✅ корректно (243KB last frame) | ❌ **чёрный экран** (10KB) |
| scene_02 | cashflow_timeline · svg_path_drawing | ✅ корректно (410KB) | ✅ идентично (408KB) |
| scene_04 | reserve_gauge · clip_path_reveal + counter | ✅ 100% оранжевый | ✅ идентично |

Метаданные mp4 у обоих движков идентичны:
- h264, 1080×1920, 30fps, 5.0с, 150 кадров
- Разница только в bitrate: наш ~440k, npx ~880k (×2)

## Корень несовместимости

`CLAUDE.md` в HF-проекте:
> «Elements with timing **MUST** have `class="clip"` — the framework uses this
> for visibility control»

Все 6 наших сцен (subagent-generated) **НЕ имеют** `class="clip"` и
`data-track-index`:

```
$ grep -c 'class="[^"]*\bclip\b' scene_*.html
scene_01   0
scene_02   0
scene_03   0
scene_04   0
scene_05   0
scene_06   0
```

scene_02 и scene_04 работают в npx «случайно» — у них вся анимация на GSAP
opacity/transform на корневом контейнере, без opt-in HF clip-visibility.
scene_01 использует CSS preserve-3d/backface-visibility на дочерних
карточках — npx-clip-system их скрывает.

## Почему НЕ заставлять subagents писать `class="clip"`

Альтернатива «B»: расширить style_contract.json + промпт чтобы subagents
всегда генерили HF-совместимые атрибуты на каждом timed element.

Отверг по 4 причинам:

1. **Дополнительная сложность промпта** — он уже 2700+ символов на сцену,
   добавление clip-visibility-правил снова раздует.
2. **Новый класс багов**: subagent забывает `class="clip"` на 1 из 50
   элементов сцены → элемент выпадает в npx → плохой UX без явной ошибки.
3. **Слабый ROI**: единственное преимущество npx — ×2 bitrate в mp4. Нам это
   не нужно, для разлива в TG достаточно нашего 440k.
4. **Чёрный ящик**: gotcha `seek vs progress` мы поймали потому что свой
   render написан нами. В npx такие баги были бы невидимы.

## Что делаем в Phase 1 Step 6 (интеграция)

В `hyperframes_broll.py` функция `_render_all` сейчас вызывает
`npx --yes hyperframes@0.6.56 render`. Заменяем на вызов
`tools/render_scene.mjs` (через node + puppeteer-core).

Зависимости на сервере:
- `node` уже стоит (нужен для нашего layout-детектора)
- `puppeteer-core` уже в `hyperframes_assets/package.json` как dep
- `chrome-headless-shell` уже скачан для детектора
- `ffmpeg` уже стоит (нужен для existing video_assembler)

То есть **дополнительных установок 0**. Только refactor `_render_all`.

## Что НЕ делаем сейчас

- Не удаляем `npx hyperframes` из проекта полностью — он остаётся как
  fallback и для `npm run dev` (preview-сервер для ручного дебага).
- Не меняем `package.json` HF-проекта.
- Парити-тест автоматизация — отложена, для регрессии достаточно
  ручного запуска через `tools/render_parity_test.py` (если когда-то
  решим вернуться к этому решению).

## Ссылки

- `D:\AI\hf_local_diag\renders\scene_01_npx_last.png` — доказательство (чёрный экран)
- `D:\AI\hf_local_diag\renders\scene_01_last.png` — наш render (корректный)
- `D:\AI\maksim-bot\tools\render_scene.mjs` — production-render (перенесён 5 июня)
- `reference_gsap_seek_vs_progress.md` (память) — почему мы вообще проверяли это
- `docs/hyperframes_production_plan_review_2026-06-04.md` — Phase 1 Step 4
