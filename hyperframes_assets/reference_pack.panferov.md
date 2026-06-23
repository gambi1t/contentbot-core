# HyperFrames Reference Pack — автономная генерация B-roll (panferov, tenant-нейтральный)

> Curated-выжимка из HyperFrames-скилла (`.agents/skills/hyperframes/`: SKILL.md, house-style.md,
> visual-styles.md, data-in-motion.md, motion-principles.md, references/techniques.md,
> references/transitions.md, references/transitions/) + полный синтез арсенала
> (`D:\AI\hyperframes_arsenal_panferov.md`) + закрытые enum валидатора
> (`storyboard_validator.py`).
>
> Цель пака — дать Claude (фазы 1-2) ПОЛНЫЙ визуальный вокабуляр и анти-паттерны
> БЕЗ форсированной тематики. Тема и тон приходят из **контекста канала** +
> сценария; пак — нейтральный арсенал, который работает для любой ниши.
>
> При конфликте приоритет: `style_contract.<tenant>.json` (палитра/типографика)
> > этот pack > общая интуиция.

---

## 0. КОНТЕКСТ КАНАЛА (panferov)

- **Канал:** panferov.ai — авторский блог Артёма про AI, автоматизацию,
  контент-производство (студия + эксперимент «соло+ИИ»).
- **ЦА:** предприниматели, практики (~30-45), технооптимисты — те, кто строит
  свой продукт/контент и хочет ИИ-рычаг, а не «волшебную таблетку».
- **Тон голоса:** прямой, без корпоративной мягкости, инженерная подача
  (примеры → числа → вывод), без хайпа и без «нейронок».
- **Визуальная база (palette токены, конкретные HEX в `style_contract.panferov.json`):**
  `bg_primary` (глубокий navy), `bg_secondary` (приглушённый navy), `accent`
  (холодный azure-голубой), `text_primary` (soft-white), `text_muted`
  (cool-grey). Акцентный диапазон — холодные голубые/azure тона. Тёмная
  атмосфера с холодным акцентом (внутренний preset, см. `style_contract.panferov.json`).
- **Шрифты:** Inter Tight (heading) + Inter (body), cyrillic+latin, локально
  через @font-face (6 woff2).
- **Рекомендуемые стили под канал (но НЕ принудительно):** `data_drift` (ядро —
  буквально «AI/data»), `swiss_pulse` (метрики, технические разборы),
  `shadow_cut` (драматичные раскрытия, до/после), `maximalist_type` (CTA,
  анонсы). Выбор финального стиля сцены — от смысла фрагмента сценария, а
  не от темы канала.

Контекст задачи в пайплайне: 6 вертикальных вставок 1080×1920, 5 сек каждая,
тёмная тема, монтаж — split-layout с аватаром → реальная видимая зона ≈ центр
(см. SAFE-AREA в §2).

---

## 1. HARD RULES (детерминизм — нарушение = битый рендер)

- НЕ использовать `Math.random()`, `Date.now()`, `new Date()`, `performance.now()`
  — рендер должен быть детерминированным (один вход = одинаковые кадры).
  Для «случайности» — seeded PRNG (mulberry32 / sfc32) с фиксированным seed
  из `getVariables()`.
- НЕ `repeat: -1` (бесконечный повтор ломает capture). Считай точное число
  повторов из длительности сцены: `repeat: Math.ceil(duration/period) - 1`
  (либо `yoyo:true` с расчётным числом повторов для палиндромных петель).
- НЕ твинить `display` / `visibility`. Только `opacity` / `scale` / transform
  (`translate`, `rotate`, `skew`).
- НЕ exit-анимации, КРОМЕ финальной сцены (scene_06). Mid-video exit =
  артефакт исчезновения.
- НЕ `<br>` для переноса. Используй `max-width` + естественный wrap.
- НЕ `async/await` в построении таймлайна. Только синхронно. Это распространяется
  и на инициализацию аудио (никаких `await Tone.start()` в init).
- НЕ отдельные `requestAnimationFrame`-циклы для частиц/фона — позиции должны
  быть функцией от `tl.time()` или твинами внутри зарегистрированного
  таймлайна. Иначе seek не отрисует тот же кадр.
- Регистрируй КАЖДЫЙ таймлайн: `window.__timelines["scene_NN"] = tl` где
  `tl = gsap.timeline({ paused: true })`. Без этого нет детерминированного seek.
- @font-face КОПИРУЙ из index.html (6 woff2, cyrillic+latin) — без кириллических
  шрифтов русский текст не отрисуется.
- Только локальные ассеты. НЕ внешние URL / fetch в HTML (недетерминизм
  рендера + сетевые срывы).
- Background-color на смысловых блоках задавай ЛИТЕРАЛЬНЫМ значением
  (не `var(--…)`), если рядом могут жить shader-переходы (html2canvas не
  резолвит CSS-переменные стабильно).
- В градиентах не использовать ключевое слово `transparent` — пиши целевой цвет
  с нулевой альфой (`rgba(R,G,B,0)`), иначе на shader-переходах появляется
  тёмная кайма.

---

## 2. LAYOUT (flex-column + split-layout safe-area)

- 🔴 **SAFE-AREA (наша геометрия, НЕ generic соцсетевая):** весь СМЫСЛОВОЙ
  контент — в видимом окне `x∈[40,1040]`, `y∈[480,1440]` (центр 1000×960).
  Причина: split-layout с аватаром обрезает кадр; что выше y=480, ниже y=1440
  или правее x=1040 — ИСЧЕЗНЕТ/обрежется. Декор/фон/glow могут выходить
  за safe-area, если их bbox не попадает в обрезаемые зоны критическим
  смыслом.
- 🔴 **Контейнер контента — flex-column** (SKILL.md «Layout Before Animation»):
  `display:flex; flex-direction:column; justify-content:center; gap:48px;
  padding:40px; box-sizing:border-box;`. Позиционируй через
  `position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);`.
  Внутренняя ширина: 1000 (safe-area) − 80 (padding слева+справа) = 920px;
  поэтому `max-width: 900px` в hero — это с зазором ±10px, не магия (см. §3).
- НЕ ставь `position:absolute; top:Npx` на ТЕКСТОВЫХ/смысловых блоках (Claude
  считает высоту шрифта вслепую → наложения). CSS `gap` сам даёт воздух.
- Absolute РАЗРЕШЁН для декора/SVG/glow/motion-paths/графических примитивов —
  если их bbox не выходит за видимое окно в части смысла.
- **Build the end-state first** (hero-frame static CSS), потом `gsap.from()`
  входы. Сначала рисуем финальный кадр; анимация — это «как мы туда пришли».
- **Композиция (motion-principles, важно — НЕ web-паттерны):**
  - 2 фокуса минимум: один доминанта (hero-цифра/слово), второй — поддержка
    (sub-label, accent-line, иконка). Глаз должен сразу найти главное.
  - Hero-элемент занимает 60-80% ширины safe-area (не «утоплен в центре»).
  - 3 слоя минимум: (a) фон-glow / ambient декор, (b) контент, (c)
    акценты-дивайдеры/иконки/маркеры.
  - **Anchor to edges** — структурные элементы прижаты к границам safe-area
    (hairline rules сверху/снизу, kicker в углу, дивайдер слева). НЕ
    centered-floating компоновка — это web-рефлекс.
  - **Zone-based split:** панель слева / контент справа, бар сверху, или
    верхняя треть = метка, центр = смысл, нижняя треть = поддержка.
  - **Вертикальный путь глаза (1080×1920):** НЕ Z-pattern / F-pattern (это
    web-рефлексы для широких экранов). Для вертикали правильно — `верх =
    метка/kicker` → `центр = доминанта` → `низ = резолв/поддержка`. Либо
    лёгкая диагональ верх-слева → центр → низ-справа. Два фокуса НЕ должны
    стоять рядом по горизонтали — иначе оба окажутся в центре кадра и
    убьют иерархию.
  - **Плотность 8-10 элементов на сцену** (с фоном и декором). 3 элемента =
    «пусто», 15+ = «свалка». На фоне дышит 2-5 ambient-декоративов.
  - Структурные линии анимируют `scaleX from 0` / `scaleY from 0` (рисуются
    в эфире), не появляются opacity-фейдом.

---

## 3. TYPOGRAPHY

- **Веса:** заголовки 700-900, body 300-400. Один и тот же шрифт в двух
  разных весах читается «как два шрифта».
- **Размеры (НЕ web!):** hero 80-120px, headline 60-84px, body 20-28px,
  labels/kickers 18-24px. Web-рефлекс 32-48px — для вертикального видео мелко.
- **Smart pairs:** serif + sans вместе сильнее двух sans, если бренд
  позволяет. У panferov — Inter Tight (heading) + Inter (body) — формально
  один семейный, но разные оси визуально работают.
- **Русская типографика:**
  - длинные слова, тире, неразрывные пробелы — ломают строки;
  - `hyphens:auto` для русского непредсказуем, не использовать в hero;
  - в hero дай `line-height:0.92; letter-spacing:-0.04em; max-width: 900px;`
    (значение `900px` согласовано с safe-area: 1000px видимая ширина − 80px
    padding flex-контейнера = 920px внутреннее окно; `max-width:900px`
    влезает с зазором, см. §2);
  - uppercase в Inter Tight ест ширину — всегда проверяй влезание;
  - переносы в hero — ручные `<br>`? Нет, см. §1 (запрещено) — управляй
    `max-width` так, чтобы wrap случался естественно.
- **Кинетика по буквам/словам:** kinetic_typography per-word, stagger
  `0.04-0.08s` между словами; per-letter — только для коротких 1-3 word
  payoff (CTA, заголовок-удар), иначе сцена «звенит».
- **Variable-font axis:** анимируй `font-weight` / `font-stretch` /
  `font-variation-settings` в рантайме — мощный приём для «одно слово
  набирает вес».
- **Tabular-nums** для счётчиков и метрик: `font-variant-numeric: tabular-nums;`
  иначе цифры «дёргаются» при counter 0→N.

---

## 4. MOTION (вокабуляр ПРОТИВ монотонности)

### 4.1. Easing = эмоция (НЕ один power2.out на всё)

| Easing | Эмоция | Когда |
|---|---|---|
| `expo.out` | Уверенно, технологично | Hero-вход цифры/слова |
| `power3.out` | Структурно, чисто | Структурные элементы, hairline rules |
| `power2.out` | Базовый дефолт | Контент, карточки (но НЕ всё подряд) |
| `sine.inOut` | Мечтательно, плавно | Ambient breathe, glow-пульс |
| `elastic.out` | Игриво, неожиданно | Финальный CTA, payoff |
| `back.out(1.2)` | Перелёт, бренд-уверенность | Badge-плашки, статичные значки |
| `power4.in` | Резкий выход | Финальная сцена exit |

Правило: не более **2 твинов с одним ease в сцене**. Вари ease как веса
шрифта — это вторая ось ритма.

⚠️ **`back.out` НЕ совмещается с tabular-nums counter 0→N.** Перелёт на
пике твина ломает читаемость цифры (читатель ловит число в момент overshoot
— оно «дрожит»). Для счётчиков используй `expo.out` или `power3.out`. Back
оставь для badge / иконок / нечисловых значков.

### 4.2. Направление ease

- `.out` → для **входов** (быстро→замедление, как «приехало»).
- `.in` → для **выходов** (только финальная сцена).
- `.inOut` → для **перемещений** на месте (camera pans, slow drifts).

### 4.3. Speed bands (НЕ одна скорость)

| Band | Длительность | Энергия | Пример |
|---|---|---|---|
| fast | 0.15-0.30s | Энергия, удар | Snap-reveal hero-цифры |
| medium | 0.30-0.50s | Контент | Карточки, входы списка |
| slow | 0.50-0.80s | Вес, важность | Hairline rules, hero-build |
| very-slow | 0.80-2.00s | Кинематограф, breathe | Ambient glow pulse |

Самая медленная сцена должна быть **×3 медленнее** самой быстрой по
ощущению. Не вся сцена одной полосы — внутри сцены смешивай (fast вход +
slow breathe + medium exit).

### 4.4. Трёхфазная дуга сцены build / breathe / resolve

- **build (0-30%)**: элементы входят stagger в порядке важности (не
  DOM-порядка). Вся последовательность входов < 500ms, входы перекрывают
  друг друга (overlap 0.05-0.15s).
- **breathe (30-70%)**: контент жив с **ОДНИМ** ambient-motion (radial
  glow дышит, ghost-text дрейфует, hairline пульсирует scaleX). Один —
  не два, иначе сцена «суетится».
- **resolve (70-100%)**: либо стоп (мощно после движения), либо exit
  (только scene_06), либо micro-shift (camera push 1-2%, color-temp +5%).
- **Momentum carry-over (важно — иначе сцена «спотыкается» на стыке фаз):**
  build → breathe НЕ через паузу, а через инерцию. Последний build-твин
  заканчивается одновременно со стартом ambient-петли breathe, и
  ambient-параметры стартуют в направлении движения build (если последний
  вход был slide-up — ambient hairline стартует с лёгким scaleX-ростом
  вверх). Это «склейка инерции». Аналогично breathe → resolve: micro-shift
  resolve начинается за 0.1-0.2s ДО формального конца breathe, перекрывая.

### 4.5. Stagger

- В порядке **важности**, не DOM-порядка. Hero первым, поддержка следом,
  декор последним.
- Stagger `0.05-0.12s` — стандарт. >0.2s — «вяло».
- Перекрывай: пока первый элемент ещё «доезжает», второй уже стартует.
- Один stagger-паттерн на сцену; в следующей сцене — другой.

### 4.6. Asymmetry входов/выходов

- Карточка появляется 0.4s, исчезает 0.25s (выход быстрее входа).
- Это правило бьёт ровно по монотонности: симметричные in/out выглядят
  «мёртво». Жизнь — в разнице.

### 4.7. Старт сцены ≠ t=0

Каждая сцена начинает первый твин с оффсетом **0.10-0.30s** от t=0. Прямой
старт в t=0 «жжёт» зрителя — нужен микро-вдох.

### 4.8. Ambient motion (фон не пуст) — детерминированные петли

2-5 ambient-декоративов как **GSAP-твины внутри зарегистрированного
таймлайна сцены** (НЕ отдельные rAF-циклы, НЕ `repeat:-1`):

- `radial glow` — scale 1.0↔1.05 + opacity 0.7↔1.0, период `T=3-6s`,
  `repeat: Math.ceil(duration/T) - 1`, `yoyo:true`, `ease:"sine.inOut"`;
- `ghost-text` (тематические слова 3-8% opacity) — translateX дрейф 20-40px,
  период 8-15s, аналогичный расчёт repeat;
- `hairline rules` — scaleX 0.95↔1.0 или opacity-пульс, период 2-4s;
- `grain/noise overlay` — opacity 4-8% дрейф, период 4-8s;
- `floating particles` (Canvas) — slow drift, < 30 частиц, позиции =
  **функция от `tl.time()`** (не от `performance.now()` и не отдельный
  rAF), seeded PRNG для начальных координат.

Формула петли (универсальная): для сцены длительности `D` и периода
ambient-петли `T` ставь `repeat: Math.ceil(D / T) - 1` (или больше с
запасом, всё равно срежется по duration сцены). Без этого либо `repeat:-1`
(запрещён §1), либо ambient-слой замирает на втором цикле.

Без этого слоя сцена «пуста при входе» — глаз видит только текст в пустоте.

---

## 5. ARCHETYPES (два слоя: ЧТО показываем × КАК реализуем)

### 5.1. Слой 1 — business_archetype (закрытый enum валидатора, 12 шт.)

Этот enum **менять нельзя** — `storyboard_validator.py:BUSINESS_ARCHETYPES`.
Описания ниже — tenant-нейтральные: формулируются через **смысл фрагмента**
сценария, не через тематику канала.

| Архетип | Что показывает (нейтрально) | Когда выбирать |
|---|---|---|
| **hero_number** | Одна большая цифра + короткий вывод | Любая метрика, ×N, %, длительность, количество |
| **before_after_cards** | Две карточки сравнения «было / стало» | До-после внедрения, состояние А vs Б |
| **cashflow_timeline** `[CHART]` | Линия по периодам (динамика во времени) | Рост числа постов по неделям, накопление часов сэкономлено по дням, темп релизов по месяцам |
| **reserve_gauge** | Шкала/термометр накопления чего-либо | Прогресс автоматизации, % покрытия, заполненность очереди |
| **checklist** | 3-5 действий с маркерами | «Что делать», шаги, чек-лист условий |
| **risk_matrix** | Риск / последствие / контроль (2×2 или строка) | Что доверить кому, на что закрывать глаза, развилки |
| **table_snapshot** `[CHART]` | Мини-таблица 3 строки × 2-3 колонки | Сравнение опций, snapshot состояния, taxonomy |
| **formula_card** | Простая формула A × B = C | Любое «X помножить на Y равно Z», правило большого пальца |
| **stack_layers** | Слои системы/процесса (вертикальный стек) | Архитектура пайплайна, слои метода, иерархия |
| **calendar_grid** | Сетка периодов (месяцы/недели/дни) | Сетка планирования, ритм публикаций, окно «когда что» |
| **path_map** | Путь от точки A в точку B | Data-flow, customer journey (проблема→решение), дорожная карта, last-mile, маршрут логики |
| **final_cta** | Финальный призыв/итог (ТОЛЬКО scene_06) | Закрытие ролика, вопрос в кадр, ссылка/жест |

**Замечания tenant-нейтральности:**

- `cashflow_timeline` — НЕ обязательно деньги. Это «динамика по времени» как
  серия точек: рост числа постов по неделям, накопление часов сэкономлено
  по дням, темп релизов по месяцам. НЕ для «длительность одной стадии» —
  это одно число, не временной ряд.
- `reserve_gauge` — НЕ обязательно резерв в рублях. Это «накопление к цели»:
  % автоматизации, заполненность бэклога, прогресс к milestone.
- `calendar_grid` — НЕ обязательно сезонность. Это «сетка периодов»: ритм
  публикаций, окна работы, slot-grid. Снят `[CHART]`-маркер — сетка периодов
  по визуальной сложности ближе к `stack_layers`, чем к временному ряду.
- `path_map` — путь A→B на любом смысле: пайплайн данных (вход→агент→выход),
  путь клиента (проблема→решение), дорожная карта релизов, метафорический
  «last-mile». Выбирай метафору, которая ложится на смысл фрагмента, а не
  на тему канала.
- Никакой жёсткой привязки к нише — архетип выбирается **под смысл бита**
  сценария.

**Правила разнообразия (гейтит storyboard_validator):**

- Соседние сцены ≠ архетип (нельзя scene_02 и scene_03 оба `hero_number`).
- ≥5 уникальных архетипов из 6 сцен.
- `[CHART]`-архетипы (`cashflow_timeline`, `table_snapshot`) суммарно ≤2 на
  ролик.
- Финал = `final_cta` на `scene_06` (или последней сцене, если N != 6).
- **≥2 уникальных `density`** на ролик (MIN_UNIQUE_DENSITY=2 в валидаторе).
- **≥2 уникальных `scale_profile`** на ролик (MIN_UNIQUE_SCALE=2).
- Не 3 подряд с одинаковым `density` / `scale_profile` (это отдельное
  правило поверх уникальности).

### 5.2. Слой 2 — hf_technique (11 техник, КАК реализуем)

Свободная строка, но используем эти 11 имён (из `references/techniques.md`):

1. **svg_path_drawing** — путь рисует сам себя (stroke-dashoffset → 0). Для
   диаграмм, коннекторов, бренд-марок, контуров, пайплайнов.
2. **canvas_procedural** — частицы/шум/поля данных через Canvas2D. «Поле
   точек», «дрейф»; ≤30 частиц для веса < 5MB. Позиции — функция от
   `tl.time()`, не от `performance.now()`.
3. **css_3d_transforms** — повороты/флипы/глубина (`perspective`,
   `rotateX/Y`, `translateZ`). Card-flip, parallax, кубы.
4. **kinetic_typography** — слова появляются по одному (per-word), stagger
   `0.04-0.08s`. Самый частый «эмоция-носитель».
5. **lottie_animation** — векторные анимации/иконки через
   `window.__hfLottie`. Премиум-иконография, micro-illustrations.
6. **video_compositing** — реальное видео внутри композиции (для panferov
   обычно мало — приоритет графики).
7. **character_typing** — терминальный набор + мигающий курсор. Для
   «команда печатается», промпт-эффект.
8. **variable_font_axis** — оси шрифта анимируются в рантайме (weight,
   stretch, optical size). Слово «набирает вес» / «худеет».
9. **gsap_motionpath** — движение объекта по SVG-пути. Частица течёт по
   пайплайну, маркер бежит по карте, точка по диаграмме.
10. **velocity_matched_transitions** — непрерывное движение «камеры»: одна
    сцена заканчивается в ту же сторону, в которую следующая стартует.
    Внутри сцены — match-move между блоками.
11. **audio_reactive** — свойства привязаны к частотам аудио. **Только в
    детерминированном режиме:** pre-baked FFT data в JSON-файле, читается
    синхронно из `getVariables()`, маппится на твин-keyframes. Никаких
    `await Tone.start()` / live Web Audio API в init (§1 запрещает async).
    Запрет на визуальном уровне: equalizer/waveform/строб (визуальный шум).
    Можно: тонкая модуляция glow-интенсивности, пульс radial на басах,
    micro-shake при пиках.

### 5.3. Комбо «энергия → техники»

| Энергия сцены | Рецепт комбо |
|---|---|
| **High-impact** (CTA, payoff, удар) | kinetic_typography + velocity_matched_transitions + counter_animation |
| **Cinematic** (драма, раскрытие) | svg_path_drawing + video_compositing + css_3d_transforms |
| **Technical** (метод, разбор) | character_typing + canvas_procedural + gsap_motionpath |
| **Premium** (вес, важно) | variable_font_axis + lottie_animation + slow speed band |
| **Data-driven** (метрики, сравнение) | canvas_procedural + counter (0→N) + svg_path_drawing |

«Технику ради техники» — нет. Техника подбирается ПОД архетип и ПОД смысл
бита.

### 5.4. Motion families (8, закрытый enum валидатора)

`storyboard_validator.py:MOTION_FAMILIES`. Это **семьи движения** — обёртки
над техниками:

| Family | Что внутри |
|---|---|
| `counter_build` | Счётчики 0→N (counter_animation + tabular-nums) |
| `kinetic_type` | Per-word stagger, variable-font axis |
| `path_draw` | SVG stroke-dashoffset → 0, диаграммы, коннекторы |
| `radial_pulse` | Concentric rings, glow-пульс, orbit-marks |
| `card_flip` | 3D-flip пар карточек, before/after |
| `vertical_stack` | Слои въезжают снизу-вверх, stack_layers |
| `snap_reveal` | Резкий вход (0.15-0.25s) + стоп |
| `slow_breathe` | Очень медленный ambient (0.8-2.0s), one-element |

Гейт: ≥4 уникальных motion_family на 6 сцен.

---

## 6. VISUAL STYLES (8 именованных, закрытый enum валидатора)

`storyboard_validator.py:VISUAL_STYLES`. Каждый стиль = пакет
(motion-character, atmosphere, ритм, опц. shader-пара для переходов).

| Стиль | Mood | Под какой контент | Motion-характер | Shader-пара (переход) |
|---|---|---|---|---|
| **swiss_pulse** | Клинично, точно | Метрики, dev-tools, SaaS-разбор, API | Энергия high, `expo.out` входы, hard cuts | Cinematic Zoom / SDF Iris |
| **velvet_standard** | Премиум, свет и пространство, luxury | Luxury, enterprise, keynote, инвестор-дек | Энергия calm, `sine.inOut`, длинные holds, светлая палитра с воздухом | Cross-Warp Morph |
| **deconstructed** | Индустриально, сыро | Tech-launch, security, punk, dev-сцены | Энергия high, glitch-вход, chromatic accent | Glitch / Whip Pan |
| **maximalist_type** | Громко, кинетично | Анонсы, запуски, CTA, payoff | Энергия high, кинетическая типографика на весь кадр | Ridged Burn |
| **data_drift** | Футуристично, иммерсивно | AI/ML, передовой tech, поля данных | Энергия medium, drift-частицы, radial glow «дышит» | Gravitational Lens / Domain Warp |
| **soft_signal** | Интимно, тепло | Wellness, личные истории, brand-story | Энергия calm, `sine.inOut`, длинные fade | Thermal Distortion |
| **folk_frequency** | Культурно, ярко | Consumer, еда, сообщества, лайфстайл | Энергия medium, цветные акценты, ритмика «как от руки» (см. ниже) | Swirl Vortex / Ripple Waves |
| **shadow_cut** | Тёмно, кинематографично, тень и контраст, exposé | Драматичные раскрытия, до/после, exposé | Энергия medium, длинная тень, slow build → snap-reveal | Domain Warp |

**Velvet vs Shadow_cut (часто путают):** `velvet_standard` = свет и
пространство (luxury keynote, белое-на-белом с тонкими тенями, воздух);
`shadow_cut` = тень и контраст (драматичный экспозе, тёмная сцена с
резким светом). Оба calm/премиум, но эмоционально противоположны.

**Folk_frequency — рецепт «как от руки» (без Math.random, §1):**

- seed-noise: detuned SVG-кривые через seeded PRNG (mulberry32) с фиксом
  начального seed из `getVariables()` — каждая «неровность» считается
  один раз и кэшируется в DOM, не пересчитывается в рантайме;
- либо предварительно нарисованные SVG-пути с «человеческим» дрожанием,
  встроенные в HTML как литерал (никакого рантайм-рандома);
- цветные акценты — литералы в палитре стиля, не процедурная генерация.

**Правила выбора (нейтральные):**

- Стиль выбирается **под смысл сцены**, не под канал. Сцена-метрика → swiss_pulse,
  сцена-payoff → maximalist_type, сцена-история → soft_signal.
- ≥3 разных стиля на 6 сцен (гейт валидатора).
- **Не миксуй CSS-transitions и shader-transitions** внутри одной композиции —
  если решили шейдеры, ВСЕ переходы шейдерные. Это правило согласовано с §10:
  в таблице energy→primary колонки CSS и Shader разделены, выбирай **одну
  ветку на композицию**.
- **Цвета берутся из `style_contract.<tenant>.json`**, не из YAML внутри
  visual-styles.md скилла. YAML-палитры стилей в скилле — это «характер»
  стиля, но конкретные цвета у нас задаёт контракт тенанта. Никакого
  cyan-purple neon из дефолтов data_drift — он в anti-patterns (см. §7).

**Рекомендация под panferov (но НЕ форсирование):** база — `data_drift` /
`swiss_pulse` / `shadow_cut` / `maximalist_type` (для CTA). Если бит сценария
тянет в тепло — `soft_signal`; в драму — `shadow_cut`; в анонс — `maximalist_type`.

---

## 7. ANTI-PATTERNS (бьют ровно в нашу монотонность)

### 7.1. Запреты для данных (`data-in-motion.md`)

- **НЕ pie charts** — трудно сравнивать сектора, выглядит как PowerPoint.
- **НЕ multi-axis charts** — зритель не изучит пересечения за 3-5 сек.
- **НЕ 6-panel dashboards** — 2-3 метрики ок, 6+ = web-паттерн «панель
  аналитика».
- **НЕ gridlines / tick marks / legends** — визуальный шум.
- **НЕ вывод chart-библиотек** (D3, Chart.js, ECharts) — строй на GSAP +
  SVG/CSS. Готовые библиотеки тащат web-эстетику и недетерминизм.

### 7.2. AI-клише (`house-style.md`, «Lazy defaults to question»)

- **НЕ identical card grids** — одинаковые карточки повторяются. Главная
  причина монотонности.
- **НЕ всё центрировано с равным весом** — глазу некуда вести. Доминанта
  обязательна.
- **НЕ gradient text** (`background-clip:text`).
- **НЕ accent-stripes слева на каждой карточке** (web-паттерн «уведомления»).
- **НЕ cyan-on-dark / purple-to-blue neon** (дефолтное «AI-выглядящее»
  клише, везде одинаковое).
- **НЕ чистый `#000` / `#fff`** — тинтуй к accent (на 3-7%).

### 7.3. Числа и визуальный вес

- **Numbers need visual weight** — каждая цифра идёт с визуальным
  элементом. Цифра в пустоте = «текст в пустоте».
- **Cardinal числа (растут от 0):** counter 0→N обязателен, `tabular-nums`,
  GSAP-tween, округление на `Math.round`. Дополни fill-bar / progress-ring /
  animated underline.
- **НЕ-cardinal числа — другой рецепт (counter 0→N не работает):**
  - **Диапазоны** («3-5 шагов», «10-15 минут») — дискретные точки/маркеры
    появляются stagger (3 маркера → ... → 5 маркеров), либо два числа
    въезжают независимо с тире-коннектором scaleX from 0;
  - **Проценты-константы** («~70%», не растущие) — ring fill от 0 до
    указанного значения с явной паузой на финале (не пробегает дальше);
  - **Соотношения** («1:3», «×2.3») — два веса/блока разного размера
    появляются одновременно, визуально демонстрируя пропорцию (один
    блок в 3× больше другого), цифра — подпись;
  - **Приблизительные** («около», «~», «более N») — bar fill с явным
    fade-out на конце (показывает «и дальше»), либо tilde-glyph пульсирует
    sine.inOut.
- **Visual continuity** — если в соседних сценах одна тема, одинаковое
  визуальное пространство, меняется только VALUE (цифра, бар, ring) — не
  весь layout.

### 7.4. Геометрические паттерны (shader-переходы)

- Избегай transitions, рисующих видимые регулярные паттерны — сетки
  плиток, гексагональные ячейки, ровные сетки точек, blob-кружочки в линию.
  Глаз мгновенно видит сетку, и сцена выглядит «бюджетной».
- Органический шум (FBM, domain warping) — ок. Регулярная геометрия — нет.
- `Grid dissolve` / `morph circle` из §10 категории «Pattern» — использовать
  **осторожно**, только под `data_drift` / `swiss_pulse`, и только с
  органическим шумом поверх (FBM-варпом), чтобы скрыть регулярность сетки.
  Чистый grid dissolve без органики — anti-pattern.

### 7.5. Чего тут НЕТ (важно для исключения путаницы)

- **Чёрного списка образов больше нет.** Дороги, природа, цены, рубли,
  бренды, ниши, темы — НЕ запрещены на уровне пака. Уместность образа =
  смысл фрагмента + tenant + сценарий. Если тема канала — AI, а сценарий
  говорит про логистику — образ дорог уместен; пак не мешает.

---

## 8. DATA / ПРАВДИВОСТЬ

- **НЕ выдуманные точные бизнес-цифры** конкретного тенанта (например,
  «X активных пользователей в марте» если число не задано в сценарии).
  Иллюстративные проценты, диапазоны, относительные числа — допустимы и
  нужны (для visual weight, см. §7.3).
- **Источник цифр** — `script_beat` (из сценария) + явные input-данные.
  Если в beat-е нет числа — не выдумывай; выбери архетип без числа
  (`checklist`, `risk_matrix`, `formula_card` без значений).
- **НЕ изображать людей, лица, руки** — только графика / motion-design.
  Иконка-силуэт ок; реалистичный портрет — нет.
- **Каждая сцена** иллюстрирует конкретный момент сценария (`primary_text`
  привязан к `script_beat`). Не «общая иллюстрация по теме».
- **Глобальное правило памяти Артёма №1 действует и тут:** не уверен в цифре
  → не выдумывай. Лучше архетип без числа, чем фейковая метрика.

---

## 9. BACKGROUND / ATMOSPHERE PRIMITIVES

На каждой сцене — 2-5 элементов фонового слоя. Ambient GSAP-петли внутри
зарегистрированного таймлайна сцены (формула повторов — §4.8).

| Примитив | Реализация | Когда |
|---|---|---|
| **radial glow** | `radial-gradient(circle, accent 0%, bg 60%, rgba(R,G,B,0) 100%)`, scale 1.0↔1.05 + opacity 0.7↔1.0, период 3-6s, `sine.inOut`, yoyo, repeat по формуле | Везде, базовый «дыхание сцены» |
| **ghost-text** | Тематические слова, opacity 3-8%, translateX дрейф 20-40px, период 8-15s | Технический контент, data-сцены |
| **hairline rules** | 1-2px линии у верха/низа safe-area, `scaleX from 0`, `power3.out`, 0.6-0.8s + лёгкий пульс scaleX 0.95↔1.0 в breathe | Anchor-to-edges, структура |
| **grain/noise** | SVG turbulence или PNG-tile, opacity 4-8%, лёгкий translate drift | Везде, добавляет «вещественность» |
| **grid** | SVG/CSS 12-колоночная сетка, opacity 5-10%, появляется scale + opacity | swiss_pulse, dev-сцены |
| **orbit rings** | Concentric circles вокруг центра, `rotate` slow, разные радиусы | data_drift, radial_pulse |
| **floating particles (Canvas)** | <30 частиц, медленный drift, позиции = функция от `tl.time()` (не отдельный rAF), seeded PRNG для начальных координат | data_drift, technical |
| **light traces** | SVG-линии через кадр, gradient stroke (rgba(R,G,B,0) → accent → rgba(R,G,B,0)) | data_drift, путь данных |

### 9.1. Presets (композиции из примитивов)

Preset = заранее собранная композиция из 2-4 примитивов выше + параметры
палитры. НЕ обязательны, выбираются под `style_contract.<tenant>.json`.

- **kosmos** (под panferov-Nox-режим): `floating particles` (мелкие точки,
  seeded по `tl.time()`) + `radial glow` по центру + `orbit rings` slow
  rotate. Используется для intro/анонсов на data_drift / shadow_cut.

Любой канал может определить свой preset (или не использовать ни одного).
Preset — это сахар поверх примитивов, не отдельная сущность.

---

## 10. ПЕРЕХОДЫ (13 категорий из скилла + статус в нашем пайплайне)

Краткая выжимка из `references/transitions.md` + `transitions/`. По одной
строке на категорию.

| Категория | Что это | Когда |
|---|---|---|
| **Push / slide** | Сцена выталкивает предыдущую (горизонт/верт/диагональ, elastic-push, squeeze) | Между связанными точками, editorial-ритм |
| **Scale / zoom** | Zoom through, zoom out, gravity drop, 3D-flip | Climax, кинематограф, weight |
| **Reveal / mask** | Circle iris, diamond iris, clock wipe, shutter, diagonal split | Topic change, «открываем картину» |
| **Dissolve** | Crossfade, blur crossfade, focus pull, color dip to black | Wind-down, premium, outro |
| **Cover** | Staggered blocks, horizontal blinds, vertical blinds | Topic change, tech-ритм |
| **Light** | Light leak, overexposure burn, film burn | Warm, retro, organic |
| **Distortion** | Glitch, chromatic aberration, ripple, VHS tape | Tense, edgy, tech-launch |
| **Pattern** | Grid dissolve, morph circle | Tech/data, **только с органическим шумом поверх** (§7.4) |
| **Shader: warp** | Cross-warp morph, domain warp | Premium, dramatic, data_drift |
| **Shader: lens** | Gravitational lens, cinematic zoom | data_drift, dramatic |
| **Shader: light** | Light leak (shader), thermal distortion | Warm, soft_signal |
| **Shader: glitch** | Glitch (shader), chromatic split, ridged burn | Tech, deconstructed, maximalist_type CTA |
| **Shader: organic** | Ripple waves, swirl vortex | Playful, folk_frequency |

**Energy → primary (выбери ОДНУ ветку на композицию — CSS или Shader, не миксуй):**

| Energy | CSS primary | Shader primary | Duration | Easing |
|---|---|---|---|---|
| Calm | Blur crossfade, focus pull | Cross-warp, thermal distortion | 0.5-0.8s | `sine.inOut`, `power1` |
| Medium | Push slide, staggered blocks | Whip pan, cinematic zoom | 0.3-0.5s | `power2`, `power3` |
| High | Zoom through, overexposure | Ridged burn, glitch, chromatic split | 0.15-0.3s | `power4`, `expo` |

Правило: **ОДИН primary на 60-70% переходов + 1-2 accent**. Разный переход
на каждой сцене = визуальный шум. Согласуй ветку (CSS/Shader) с §6:
один тип на всю композицию.

⚠️ **Статус в нашем пайплайне (важно):** сейчас сцены рендерятся как 6
отдельных MP4 и склеиваются монтажом в `video_assembler` (cut-склейка).
Межсценовые переходы из этого раздела — **потенциал** для будущего
single-composition режима, не текущая фича. **Внутрисценовый motion весь
доступен** (build/breathe/resolve, ambient, kinetic, path_draw, audio_reactive
и пр.) — это §4. Переходы здесь — словарь, чтобы когда мы перейдём на
single-comp, не пересобирать пак с нуля.

---

## 11. ENGINE NOTES (movie/parametrization)

- **variables / параметризация** — все строки/числа сцены приходят из
  `getVariables()`; одна композиция → много рендеров через подмену
  переменных. Никакого hard-code текста в HTML.
- **sub-compositions** — каждая сцена = отдельный HTML, referenced через
  `data-composition-src="compositions/scene_NN.html"`. Композиция в корне
  склеивает их таймлайн.
- **fitTextFontSize / pretext** — анти-overflow для динамического текста.
  Считаем размер шрифта так, чтобы строка влезла в `max-width`.
- **tabular-nums** — `font-variant-numeric: tabular-nums;` на всех счётчиках
  и метриках. Без этого цифры «дёргаются» при counter 0→N.
- **timeline registration** — `window.__timelines["scene_NN"] = gsap.timeline({ paused: true });`
  (см. §1). Без этого нет детерминированного seek и hyperframes не
  отрисует фрейм.
- **clip elements** — каждый элемент с тайминговыми атрибутами требует
  `class="clip"` + `data-start` + `data-duration` + `data-track-index`.

---

## 12. ПОТОК ГЕНЕРАЦИИ (как пак используется)

1. **Фаза 1 — storyboard.json**: Claude пишет 6 сцен (см. поля в
   `_REQUIRED_FIELDS` валидатора). Каждая сцена = `business_archetype` (§5.1)
   + `hf_technique` (§5.2) + `visual_style` (§6) + `motion_family` (§5.4) +
   `density` + `scale_profile` + `primary_text` + `script_beat` + `reason`.
2. **Гейт**: `validate_storyboard()` проверяет схему + diversity-правила
   (§5.1, включая ≥2 уникальных density / ≥2 уникальных scale_profile).
   Ошибки → fix-round (`format_errors_for_claude`).
3. **Фаза 2 — HTML-сцены**: на каждый valid storyboard → 6 HTML по правилам
   §1-4, 9-11 + tenant-палитра из `style_contract.<tenant>.json`.
   ⚠️ В фазе 2 **НЕ закладывать межсценовые переходы** в HTML (никаких
   exit-state кроме `scene_06` — текущий пайплайн это cut-склейка, любая
   exit-анимация в middle-сцене даст артефакт исчезновения).
4. **Сборка**: video_assembler склеивает MP4 (сейчас cut, в будущем —
   переходы из §10 в single-composition режиме).

---

*Файл — tenant-нейтральный арсенал; конкретная палитра тенанта подключается
через `style_contract.<tenant>.json` (`load_style_contract(path)`). Канал
panferov использует внутренний preset «тёмная атмосфера + холодный azure
акцент»; остальные тенанты подменяют только контракт, не пак.*