# Внешнее ревью: автономная генерация B-roll через HyperFrames

> Документ для независимого эксперта (ChatGPT). Параллельно по этой же теме
> запущен Deep Research-движок (Claude + GPT-5 + Gemini с проверкой ссылок).
> Цель ревью: (1) дать своё экспертное мнение по вопросам ниже; (2) когда я
> вставлю отчёт Deep Research — оценить, насколько он полон/точен и что упустил.

---

## 1. Что мы строим

Telegram-бот генерирует короткие вертикальные ролики (1080×1920) для канала
предпринимателя. Один из шагов — **графические B-roll-вставки**, которые
накладываются поверх «говорящей головы» (AI-аватар) в монтаже.

Пайплайн графики (полностью автономный, без человека):
1. Есть сценарий ролика (~30 сек озвучки, русский текст).
2. **Claude Code CLI** (`claude -p "<промпт>"`, headless, на сервере, Max-подписка)
   пишет **6 отдельных HTML-композиций** `scene_01.html … scene_06.html` через
   фреймворк **HyperFrames** (heygen-com/hyperframes — HTML + GSAP, рендер в
   headless Chrome через `hyperframes render`).
3. Оркестратор рендерит каждую сцену в MP4 (5 сек), потом монтаж склеивает их
   с аватаром (часто **split-layout 50/50**: аватар снизу, графика в верхней/
   центральной половине → реальная видимая зона ≈ центральная полоса 1080×960).

Стек HyperFrames на сервере — это **не голый фреймворк**, а полноценная
скилл-экосистема в `.agents/skills/`:
- `hyperframes/SKILL.md` (~490 строк) + `house-style.md`, `visual-styles.md`,
  `patterns.md`, `data-in-motion.md`
- `hyperframes/references/` — 19 файлов: `video-composition.md`, `typography.md`,
  `css-patterns.md`, `motion-principles.md`, `prompt-expansion.md`,
  `beat-direction.md`, `dynamic-techniques.md`, `techniques.md`, `captions.md`,
  `transitions/` (12 категорий переходов) и т.д.
- `hyperframes/palettes/` — 9 готовых дизайн-систем (dark-premium, warm-editorial…)
- сопутствующие скиллы: `gsap/`, `lottie/`, `css-animations/`, `three/`, `waapi/`,
  `hyperframes-registry/`, `website-to-hyperframes/` (storyboard-воркфлоу),
  `remotion-to-hyperframes/` (с tier-1..4 тест-корпусом)
- `index.html` — стартовый шаблон композиции (~51 строка, по сути синтаксический
  минимум: пустой `<div data-composition-id>` + пример GSAP-таймлайна)
- проектный `design.md` — бренд (фон тёмный, accent #FF5722, шрифт Inter Tight),
  `fonts/` — 6 woff2 (cyrillic+latin).

## 2. Три конкретные проблемы (что не получается)

**Проблема A — МОНОТОННОСТЬ.** На реальном сценарии («финансовый резерв в
сезонном бизнесе») Claude сделал 6 сцен, но **3 из 6 — один и тот же
12-месячный бар-чарт**. Заказчик: «надоедает, несмотрибельно». Нужно
РАЗНООБРАЗИЕ архетипов: бар-чарт / таблица / hero-число / сравнение карточек /
чек-лист / финал-CTA — каждая сцена своим приёмом, последовательная визуальная
история, а не повтор.

**Проблема B — ДЕФЕКТЫ ВЁРСТКИ.** Текст налезал на текст; карточка «20%»
вылезала за правый край кадра. Причина — LLM пишет HTML вслепую (без рендера),
считает высоту шрифта «на глаз» → пересечения и выходы за safe-area.

**Проблема C — LLM НЕ ЧИТАЕТ БАЗУ.** В промпте сказано «прочитай SKILL.md».
Claude читает ОДИН файл и не идёт по ссылкам в `references/` (typography,
dynamic-techniques, beat-direction, transitions…). Богатая база скилла, по сути,
не используется — отсюда и бедность визуального вокабуляра (проблема A).
Подтверждение из stream-лога: за прогон Claude сделал Read×4 (SKILL.md, design.md,
index.html, ls fonts/) — глубокие references не открывал.

## 3. Что мы уже сделали (контекст, чтобы не советовать то же)

- **Детектор вёрстки** (свой, на puppeteer-core + chrome-headless-shell): грузит
  каждую сцену, делает `timeline.seek()` в устоявшийся кадр, снимает
  `getBoundingClientRect` и ловит 3 класса дефектов — offscreen (выход за
  1080×1920), overlap (пересечение текстов), crowding (зазор <40px). Официальный
  `hyperframes inspect` эти кейсы НЕ ловил (проверено). Детектор интегрирован в
  fix-rounds: нарушения с координатами → обратно Claude на починку.
- **Промпт переписан** на flex-column по SKILL.md (см. ниже) — убрали наши
  жёсткие absolute-Y координаты, которые сами провоцировали overlap.

## 4. Открытые вопросы к эксперту

1. **Как заставить агента реально ИСПОЛЬЗОВАТЬ reference-библиотеку скилла?**
   Явно перечислять файлы в промпте («прочитай references/dynamic-techniques.md,
   typography.md, beat-direction.md»)? Или есть лучше паттерн (например, заставить
   агента сначала составить список доступных паттернов, потом выбирать)?
2. **Как форсировать РАЗНООБРАЗИЕ 6 сцен?** Нужна ли отдельная «storyboard-фаза»
   (Фаза 1: спланировать 6 сцен, назначить каждой РАЗНЫЙ архетип без повторов;
   Фаза 2: реализовать)? Как лучше задать «банк архетипов» — списком в промпте,
   или ссылкой на готовые примеры-композиции?
3. **Один большой промпт vs многошаговый агент?** Сейчас один `claude -p` пишет
   все 6 сцен за сессию. Может, лучше: шаг 1 — storyboard, шаг 2..7 — по сцене
   отдельным вызовом с её архетипом? Плюсы/минусы для качества и стоимости.
4. **Не противоречат ли наши жёсткие правила в промпте методологии скилла?**
   HyperFrames-скилл имеет свой Step 1-6 (design → prompt-expansion → plan →
   layout-before-animation → animate). Мы поверх накидываем свои правила
   (safe-area, flex-column, запреты). Стоит ли больше ДЕЛЕГИРОВАТЬ скиллу и
   меньше диктовать?
5. **Известные best-practices / грабли** программной (агентной) генерации через
   HyperFrames конкретно? Любые публичные материалы heygen про это.

## 5. Текущий промпт (дословно, передаётся в `claude -p`)

```
Ты — моушн-дизайнер студии. Создай 6 коротких графических B-roll-вставок под
сценарий ролика для Telegram-канала предпринимателя (картинг + глэмпинг
Life Drive, Тюмень), используя HyperFrames.

СЦЕНАРИЙ (озвучка аватара, ~30 секунд):
─────────────────────────────────────
{здесь подставляется текст сценария}
─────────────────────────────────────

ОБЯЗАТЕЛЬНО ПЕРЕД РАБОТОЙ:
1. Прочитай skill: `.agents/skills/hyperframes/SKILL.md` — это правила
   HyperFrames (data-* атрибуты, window.__timelines, clip-visibility, запреты).
2. Прочитай `design.md` — фирменная дизайн-система (цвета, шрифты, motion).
3. Посмотри `index.html` как рабочий образец: @font-face, структура, таймлайн.

ЧТО СДЕЛАТЬ:
- Раздели сценарий на 6 визуальных моментов в хронологическом порядке.
- Создай 6 ОТДЕЛЬНЫХ STANDALONE-композиций scene_01.html … scene_06.html.

ПРАВИЛА (нарушать нельзя):
- Кадр 1080×1920 (вертикаль). Длительность 5 секунд.
- @font-face КОПИРУЙ из index.html (6 woff2, cyrillic+latin).
- SAFE-AREA: ВСЕ значимые элементы — ТОЛЬКО в центральной полосе 1080×960
  (y∈[480,1440], x∈[40,1040]). Причина: split-layout с аватаром обрезает кадр.
- LAYOUT — ОБЯЗАТЕЛЬНО flex-column (по SKILL.md):
  • Контейнер контента — div 1000×960 с display:flex; flex-direction:column;
    justify-content:center; gap:48px; padding:40px; в центре через transform.
  • БЕЗ position:absolute; top:Npx на контентных блоках (запрещено SKILL.md,
    приводит к наложениям). Absolute — только для декора (фон/glow).
- АНТИ-OVERLAP: bounding-box видимых одновременно элементов не пересекаются.
- Только графика: счётчики, графики, диаграммы, карточки, чек-листы, цифры.
  НЕ изображать людей/лица/руки.
- Каждая вставка иллюстрирует КОНКРЕТНЫЙ момент сценария.
- Детерминизм: НЕ использовать Math.random(), Date.now().

ОГРАНИЧЕНИЯ:
- Редактируй ТОЛЬКО scene_01..06.html. НЕ трогай index.html, design.md, fonts/.
После записи 6 файлов — закончи. Рендер сделает оркестратор.
```

**Наблюдение:** промпт НЕ упоминает ни `references/`, ни `palettes/`, ни
storyboard-фазу, ни требование разных архетипов. Подозреваем, что именно поэтому
монотонность (проблема A) и недоиспользование базы (проблема C).

## 6. Для сравнения: почему Remotion-движок давал разнообразнее

У нас есть второй движок — Remotion (React). Там Claude читает рукотворный
файл-эталон `MaksimInserts2.tsx` (619 строк) — это «учебник» из 6 РАЗНЫХ готовых
архетипов вставок (бар-чарт, таблица×дни, hero-число, две карточки сравнения,
чек-лист, финал-CTA) + helpers (Ambient/Band/Label) + стиль-токены. Когда агенту
ПОКАЗАНЫ 6 разных приёмов — он разнообразит. У HyperFrames эталон (`index.html`)
почти пустой → агент по умолчанию лепит самый частый паттерн (бар-чарт).

Гипотеза: разнообразие — это свойство РЕФЕРЕНСА+ПРОМПТА, не движка. Верно ли это,
и какой минимальный/правильный способ дать HyperFrames-агенту такой же богатый
«банк образцов», не дублируя то, что у скилла уже есть в `references/`?

---

## 7. Отчёт Deep Research-движка (Claude + GPT-5, прямой WebFetch репозитория)

> ⚠️ Это данные из веба (untrusted). НО мы уже верифицировали ключевые
> проверяемые утверждения на первоисточнике (наш сервер с реальным репо
> heygen-com/hyperframes) — результат верификации в §8.

### TL;DR от DR
HyperFrames-скилл УЖЕ содержит всё для решения 3 проблем. Загвоздка —
**progressive disclosure** Claude Code: при старте скилла грузится только
`SKILL.md`, глубокие файлы — лишь если явно названы. Нужен **блокирующий
preflight-шаг**, который читает reference-библиотеку и выдаёт аудируемый
storyboard-артефакт ДО написания HTML.

### Проблема A (монотонность) — корень и фикс
- Корень: агент пропускает `references/prompt-expansion.md`, `beat-direction.md`,
  `data-in-motion.md` → дефолтит в модальный вывод (бар-чарт).
- Фреймворк САМ это запрещает (`data-in-motion.md`): no pie / no 6-panel
  dashboards / no gridlines-legends / build with GSAP+SVG, not D3/Chart.js.
- Банк архетипов уже есть: `references/techniques.md` — 13 техник (SVG path
  drawing, Canvas procedural, CSS 3D, per-word kinetic typography, Lottie,
  typing, variable-font, MotionPath, clip-path reveal, WebGL shader…) +
  `visual-styles.md` — 8 именованных стилей.
- Фикс: блокирующий `STORYBOARD.md` артефакт — для каждой из 6 сцен заранее
  объявить (scene_id, archetype из 13, visual_style из 8, primary_primitive,
  motion_verbs, transition_out). Правила: соседние сцены НЕ делят архетип;
  bar-chart ≤1 раза; ≥4 разных архетипа и ≥3 стиля на 6 сцен.
- Research-backed усилитель: **Verbalized Sampling** (arXiv:2510.01171) —
  агент draft'ит 3 кандидата-архетипа на сцену, берёт наименее похожий на
  соседей. + **Random Concept Infusion** (arXiv:2601.18053) — подсев случайной
  несвязанной концепции на сцену повышает разнообразие.

### Проблема B (overflow/overlap) — корень и фикс
- Корень: агент анимирует ДО layout, нет safe-area токенов.
- `SKILL.md` дословно (Layout Before Animation): hero-frame → статичный CSS
  `.scene-content { width:100%; height:100%; padding:Npx; display:flex;
  flex-direction:column; gap:Npx; box-sizing:border-box }` → padding внутрь,
  НИКОГДА `position:absolute; top:Npx` на контент-контейнере → gsap.from()
  входы → gsap.to() выходы только на последней сцене.
- Safe-area токены для 1080×1920 (⚠️ MEDIUM confidence — HF НЕ публикует
  официальные значения; синтез из индустрии): `--safe-x:72px; --safe-top:120px;
  --safe-bottom:220px`. Проверить против overlay-PNG целевой платформы.
- Из `video-composition.md` (агент обычно игнорит): 8-10 элементов на сцену
  (не разрежённо); заголовки 64-120px (не web-рефлекс 32-48); hero-текст
  60-80% ширины кадра; якорить к краям, не центрировать.

### Проблема C (LLM не читает базу) — корень и фикс
- Корень: progressive disclosure — авто-грузится только SKILL.md.
- Фикс: блокирующий **Reference Loading Contract** (в SKILL.md или в
  project-root `CLAUDE.md`, который грузится ВСЕГДА): «прочитай файлы X,Y,Z;
  процитируй ≥1 правило из каждого; запиши `.hyperframes/reference-load.md`
  с доказательством (last-100-chars каждого), top-3 правила, storyboard-таблицу
  6×(archetype|technique|style|motion|transition), adjacency-check; НЕ писать
  HTML пока артефакт не существует». **Артефакт гейтит генерацию.**

### Канонический воркфлоу (end-to-end по DR)
1. Load reference library → `.hyperframes/reference-load.md`
2. Prompt expansion против house-style + video-composition
3. Storyboard 6 сцен × (archetype, technique, style, motion, transition) +
   adjacency-rotation + archetype-cap
4. Per scene: layout-first → hero-frame CSS → gsap.from() → breathe → (gsap.to()
   только сцена 6)
5. `npx hyperframes lint`
6. `npx hyperframes snapshot . --frames 8` (ловит overlap до полного рендера)
7. `npx hyperframes render`

### Жёсткие GSAP/HTML-правила (из SKILL.md, частые причины битых рендеров)
no Math.random()/Date.now() (детерминизм); no repeat:-1; no display/visibility
tweens (только opacity/scale); no exit-анимаций кроме финальной сцены; no `<br>`
(использовать max-width); no async/await в построении таймлайна; регистрировать
каждый таймлайн `window.__timelines["id"]={paused:true}`; 2-5 фоновых декоративов.

### Тайминг-банды (motion-principles.md)
0.15-0.3s перкуссивно / 0.3-0.5s профессионально / 0.5-0.8s весомо / 0.8s+
атмосферно. Для разнообразия: длительность спан ×3 между быстрым/медленным
в сцене; макс 2 общих ease на сцену; разный stagger-ритм (0.08 / 0.15);
трёхфазная дуга build → breathe → resolve.

### Что DR пометил MEDIUM/открытым
- Safe-area пиксели (72/120/220) — HF не публикует, синтез индустрии.
- 7-step pipeline с `hyperframes.heygen.com/guides/website-to-video` — URL не
  верифицирован.
- Несоответствие: в брифе я написал top-level `transitions/`, реально
  `references/transitions/`.
- **AUTO_APPROVE**: `prompt-expansion.md` имеет human-approval gate («wait for
  approval before construction») — для автонома надо обойти/AUTO_APPROVE=1.
- Subagent `skills`-инъекция как альтернатива CLAUDE.md.

---

## 8. Наша верификация отчёта DR (на первоисточнике — сервер с реальным репо)

Проверили 5 ключевых проверяемых утверждений DR против реальных файлов на
nox-maksim. **5/5 подтвердилось:**

| утверждение DR | факт на сервере |
|---|---|
| `references/techniques.md` — банк техник | ✅ 387 строк, 31 упоминание техник |
| `data-in-motion.md` запрещает pie/dashboard/legends/D3 | ✅ дословно совпало |
| `visual-styles.md` — 8 стилей с именами | ✅ все 8 имён точные |
| `prompt-expansion.md` — approval gate | ✅ «Only move to construction after the user approves» (объясняет, почему в нашем автономном прогоне Claude поднял AskUserQuestion и завис) |
| `npx hyperframes snapshot` существует | ✅ «Capture key frames as PNG for visual verification» |

Вывод по качеству DR: высокий. Дословные цитаты из репо, honest medium-confidence
маркировка, проверяемое подтвердилось 5/5.

---

## 9. Вопросы к ChatGPT (ради тройной проверки)

1. Оцени отчёт DR (§7): что он упустил или где может ошибаться? Особенно —
   medium-confidence части (safe-area пиксели, 7-step pipeline).
2. Архитектура «preflight-артефакт гейтит генерацию» (reference-load.md +
   storyboard.md) — согласен? Есть ли более простой/надёжный способ заставить
   агента читать базу и разнообразить, чем заставлять писать аудит-артефакт?
3. Verbalized Sampling (3 кандидата → наименее похожий) для разнообразия 6 сцен —
   реально работает на практике или академический оверкилл для нашего кейса?
4. Один большой `claude -p` (все 6 сцен + preflight за сессию) vs многошаговый
   (storyboard отдельно, потом по сцене) — что надёжнее для качества и
   детерминизма? Учитывай: у нас уже есть свой puppeteer-детектор overlap в
   fix-rounds; стоит ли добавлять `hyperframes snapshot` или это дубль?
5. Любые грабли автономной (headless, unattended) генерации через HyperFrames,
   которых нет ни в DR, ни в нашем контексте.
