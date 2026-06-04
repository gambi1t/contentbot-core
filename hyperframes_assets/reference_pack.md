# HyperFrames Reference Pack — автономная генерация B-roll (Life Drive)

> Curated-выжимка из HyperFrames-скилла (`.agents/skills/hyperframes/`),
> собрана оркестратором, чтобы Claude гарантированно видел визуальный
> вокабуляр и анти-паттерны (не полагаясь на progressive disclosure).
> Все правила — из реальных файлов скилла: SKILL.md, house-style.md,
> motion-principles.md, data-in-motion.md, techniques.md, visual-styles.md.
> При конфликте: `design.md` бренда > этот pack.

Контекст задачи: 6 вертикальных графических вставок 1080×1920, тёмная тема,
accent #FF5722, шрифт Inter Tight (cyrillic+latin из `fonts/`). Каждая 5 сек.
Монтаж — split-layout с аватаром, поэтому реальная видимая зона ≈ центр.

---

## 1. HARD RULES (детерминизм — нарушение = битый рендер)

- НЕ использовать `Math.random()`, `Date.now()`, `new Date()`, `performance.now()`
  — рендер должен быть детерминированным (один вход = одинаковые кадры).
- НЕ `repeat: -1` (бесконечный повтор ломает capture). Считай точное число
  повторов из длительности.
- НЕ твинить `display` / `visibility`. Только `opacity` / `scale` / transform.
- НЕ exit-анимации, КРОМЕ финальной сцены (scene_06). Mid-video exit =
  артефакт исчезновения.
- НЕ `<br>` для переноса. Используй `max-width` + естественный wrap.
- НЕ `async/await` в построении таймлайна. Только синхронно.
- Регистрируй КАЖДЫЙ таймлайн: `window.__timelines["scene_NN"] = tl` где
  `tl = gsap.timeline({ paused: true })`. Без этого нет детерминированного seek.
- @font-face КОПИРУЙ из index.html (6 woff2, cyrillic+latin) — без кириллических
  шрифтов русский текст не отрисуется.
- Только локальные ассеты. НЕ внешние URL / fetch в HTML (недетерминизм рендера).

## 2. LAYOUT (flex-column + наша split-layout safe-area)

- 🔴 SAFE-AREA (наша геометрия, НЕ generic соцсетевая): весь СМЫСЛОВОЙ контент
  в видимом окне `x∈[40,1040]`, `y∈[480,1440]` (центр 1000×960). Причина:
  split-layout с аватаром обрезает кадр; что выше y=480, ниже y=1440 или
  правее x=1040 — ИСЧЕЗНЕТ/обрежется.
- 🔴 Контейнер контента — flex-column (SKILL.md «Layout Before Animation»):
  `display:flex; flex-direction:column; justify-content:center; gap:48px;
  padding:40px; box-sizing:border-box;`. Позиционируй через `position:absolute;
  left:50%; top:50%; transform:translate(-50%,-50%);`.
- НЕ ставь `position:absolute; top:Npx` на ТЕКСТОВЫХ/смысловых блоках (Claude
  считает высоту шрифта вслепую → наложения). CSS gap сам даёт воздух.
- Absolute РАЗРЕШЁН для декора/SVG/glow/motion-paths/графических примитивов —
  если их bbox не выходит за видимое окно.
- Build the end-state first (hero-frame static CSS), потом `gsap.from()` входы.
- Композиция (motion-principles): 2 фокуса минимум, hero-текст 60-80% ширины,
  3 слоя минимум (фон-glow / контент / акценты-дивайдеры), **anchor to edges**
  (не centered-floating — это web-паттерн), zone-based split (панель слева,
  контент справа / бар сверху), структурные элементы (rules, dividers — анимируют
  `scaleX from 0`).

## 3. TYPOGRAPHY

- Заголовки 700-900 веса, body 300-400. Заголовки 60px+ (часто 80-120px для
  hero), body 20px+. (Web-рефлекс 32-48px — мелко для видео.)
- Serif + sans вместе лучше двух sans (если бренд позволяет; у нас Inter Tight).
- Русская типографика: длинные слова, тире, неразрывные пробелы. `hyphens:auto`
  для русского непредсказуем — в hero-тексте не допускай длинных строк, дай
  `line-height:0.92; letter-spacing:-0.04em; max-width`. uppercase Inter Tight
  ест ширину — проверяй влезание.

## 4. MOTION (вокабуляр ПРОТИВ монотонности — motion-principles.md)

Guardrails (ты их знаешь и нарушаешь):
- НЕ один ease на всё (дефолт `power2.out`). Вари как веса шрифта — не более 2
  твинов с одним ease в сцене. **Easing = эмоция**: `expo.out`=уверенно,
  `sine.inOut`=мечтательно, `elastic.out`=игриво.
- `.out` для входов (быстро→замедление), `.in` для выходов, `.inOut` для
  перемещений. (Ты путаешь — ease-in для входов вял.)
- НЕ одна скорость. Самая медленная сцена 3× медленнее самой быстрой.
  Speed bands: fast 0.15-0.3s (энергия) / medium 0.3-0.5s (контент) /
  slow 0.5-0.8s (вес) / very-slow 0.8-2.0s (кинематограф).
- НЕ один stagger на все сцены — у каждой свой ритм. Stagger в порядке
  важности (не DOM-порядка), вся последовательность <500ms, входы перекрывай.
- НЕ ambient-zoom на каждой сцене — вари ambient (pan / rotation / scale push /
  color shift / стоп). Стоп после движения мощен.
- НЕ старт в t=0 — оффсет 0.1-0.3s.
- Asymmetry: входы дольше выходов (карточка 0.4s появляется, 0.25s исчезает).
- 🔴 Каждая сцена = 3 фазы **build / breathe / resolve**: build (0-30%) элементы
  входят stagger; breathe (30-70%) контент жив с ОДНИМ ambient-motion;
  resolve (70-100%) выход/финал (только scene_06) или стоп.
- Background layer: 2-5 декоративов с ambient GSAP (radial glow, ghost-text
  3-8% opacity, hairline rules, grain). Без них сцена пуста при входе.

## 5. ARCHETYPES (два слоя: ЧТО показываем × КАК реализуем)

### Слой 1 — business_archetype (под бизнес-контент, enum валидатора)
- **hero_number** — одна большая цифра + короткий вывод.
- **before_after_cards** — две карточки сравнения (было / стало).
- **cashflow_timeline** — линия месяцев/сезонов (выручка/расходы). [CHART]
- **reserve_gauge** — шкала/термометр накопления резерва.
- **checklist** — 3-5 действий с маркерами.
- **risk_matrix** — риск / последствие / контроль.
- **table_snapshot** — мини-таблица 3 строки. [CHART]
- **formula_card** — простая формула (A × B = C).
- **stack_layers** — слои системы/процесса.
- **calendar_grid** — сезонность/месяцы сеткой. [CHART]
- **path_map** — путь от проблемы к решению.
- **final_cta** — финальный призыв/итог (ТОЛЬКО scene_06).

Правила разнообразия (гейтит storyboard_validator): соседние сцены ≠ архетип;
≥5 уникальных архетипов из 6; [CHART]-архетипы (cashflow/table/calendar)
суммарно ≤2; финал = final_cta.

### Слой 2 — hf_technique (КАК, techniques.md)
svg_path_drawing, canvas_procedural, css_3d_transforms, kinetic_typography
(per-word), character_typing, variable_font_axis, gsap_motionpath,
velocity_matched_transitions, clip_path_reveal, counter_animation. Выбирай
технику ПОД архетип, не «технику ради техники».

## 6. VISUAL STYLES (8 именованных, visual-styles.md — вари по сценам)

- **swiss_pulse** — клинично, точно (метрики, данные, dev-tools).
- **velvet_standard** — премиум, вне времени (luxury, enterprise).
- **deconstructed** — индустриально, сыро (tech-launch, security).
- **maximalist_type** — громко, кинетично (анонсы, запуски).
- **data_drift** — футуристично, иммерсивно (AI/ML).
- **soft_signal** — интимно, тепло (wellness, личные истории).
- **folk_frequency** — культурно, ярко (consumer, еда, сообщества).
- **shadow_cut** — тёмно, кинематографично (драматичные раскрытия).

Для нашего бренда (предприниматель, тёмная тема, оранж): база — swiss_pulse /
data_drift / shadow_cut, акценты — maximalist_type (для CTA) / velvet_standard.
≥3 разных стиля на 6 сцен.

## 7. ANTI-PATTERNS (прямо бьют в нашу монотонность)

Из data-in-motion.md (запреты для данных):
- НЕ pie charts (трудно сравнивать, выглядит как PowerPoint).
- НЕ multi-axis charts (зритель не изучит пересечения за 3 сек).
- НЕ 6-panel dashboards (2-3 метрики ок, 6+ = web-паттерн).
- НЕ gridlines / tick marks / legends (визуальный шум).
- НЕ вывод chart-библиотек — строй GSAP + SVG/CSS, не D3 / Chart.js.

Из house-style.md «Lazy defaults to question» (AI-клише — РОВНО наша болезнь):
- НЕ identical card grids (одинаковые карточки повторяются) ← наша монотонность.
- НЕ всё центрировано с равным весом (веди глаз).
- НЕ gradient text (`background-clip:text`), accent-stripes слева на карточках,
  cyan-on-dark / purple-to-blue neon, чистый #000/#fff (тинтуй к accent).

Numbers need visual weight: каждая цифра — с визуальным элементом (fill-bar,
color-shift, progress-ring), не текст в пустоте. Visual continuity: последовательные
статы одного концепта — в одном визуальном пространстве, меняется только VALUE.

## 8. DATA / ПРАВДИВОСТЬ

- НЕ выдуманные точные денежные цифры бизнеса (рубли выручки/прибыли).
  Иллюстративные проценты и числа — допустимы.
- НЕ изображать людей, лица, руки. Только графика / моушн-дизайн.
- Каждая сцена иллюстрирует КОНКРЕТНЫЙ момент сценария (primary_text из
  storyboard привязан к script_beat).
