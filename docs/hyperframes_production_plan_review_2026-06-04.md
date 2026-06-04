# Внешнее ревью: production-план автономного B-roll для maksim-bot

> Документ для адверсариального ревью независимого CTO (ChatGPT). Цель — найти
> архитектурные слабости в плане до того, как мы начнём переписывать production.
> Я (Claude, техдиректор Артёма) сегодня **дважды** отдавал результат без проверки
> и Артём ловил то, что я пропускал. Это статистический сигнал что я нуждаюсь во
> внешнем reviewer перед переписыванием. Поэтому бриф максимально честный, с моими
> косяками.

## 1. Контекст

**Maksim-bot** — Telegram контент-бот для клиента Максима (Life Drive: картинг +
глэмпинг). Один из его сценариев — автоматическая генерация **B-roll** (графических
видео-вставок 1080×1920) под TTS-голос сценария. Готовый B-roll монтируется поверх
AI-аватара Максима в 50/50 split-layout.

**Фреймворк**: HyperFrames (heygen-com/hyperframes) — HTML/GSAP композиции, рендер
через headless Chrome + FFmpeg.

**Архитектура сейчас в проде** (`hyperframes_broll.py`, ~1000 строк):
- Фаза 1: `claude -p` (headless) пишет `storyboard.json` (6 сцен с разными
  архетипами) → machine-валидатор разнообразия.
- Фаза 2: per-scene build — на КАЖДУЮ сцену отдельный `claude -p` пишет
  `scene_NN.html`. **Последовательный цикл** с retry, layout fix-rounds.
- Рендер: `npx hyperframes@0.6.56 render` × 6 сцен.
- Сборка: `video_assembler.assemble_auto_montage(layout="split", broll_mode="hf")`.

**Поток данных:** Telegram-сценарий от Максима → `_run_storyboard_phase` →
`_run_build_phase` → `_inspect_all_scenes` (наш свой puppeteer-детектор, не
официальный `hyperframes inspect`) → fix-rounds → `_render_all` → assembler →
ответ в Telegram.

**Целевой UX**: пользователь ждёт ≤ 3-5 минут на полный B-roll. **Сейчас по факту:
30-60 минут**, и часто фейлит. Это и есть главная боль.

## 2. 3 дня боли — что выяснили (1-4 июня)

Одна и та же сцена `scene_02` (cashflow_timeline + svg_path_drawing + dense) **не
генерировалась 4 прогона подряд** на сервере. Каждый раз — 3 retry × 10 мин таймаут,
ноль Write, файла нет. Я обвинял по очереди:
- 1 июня: «lint-loop из SKILL.md» — добавил жёсткий запрет в промпт. Не помогло.
- 3 июня: «промпт читает SKILL.md (45KB)» — урезал до 2765 символов. Не помогло.
- 3 июня: «таймаут 10 мин маленький» — не стал увеличивать (3 ревью предупредили
  что это маскирует). Правильное решение, но не корень.
- 3 июня: «rate-limit из шаренной подписки» — добавил `_rate_limit_note`,
  оркестратор перестал retry в стену. Помогло частично, не корень.
- 4 июня: «whitelist валидатора режет валидный HTML с GSAP CDN» — добавил
  whitelist. Помогло scene_01, но scene_02 опять упёрся.

**Корень который я НЕ видел 3 дня**: мои собственные ограничения в headless вызове.
- `--max-turns 8` — слишком мало для тяжёлой сцены (нужно 10-14).
- `Bash` запрещён в `--allowedTools` — но Claude использует Bash для разведки
  структуры (`ls`, `find`, `grep` — что где лежит). Не для lint-loop, как я думал.

**Доказательство (живой TUI-замер 4 июня)**: scene_02 запущена в interactive
Claude Code Desktop на десктопе Артёма, отдельный bucket токена. **Сработала за
8 минут 17 секунд** с одной попытки. Tools: Bash=7, Read=4, Write=1, 12 turns,
5 минут чистого thinking на SVG-планирование.

То есть **задача scene_02 была решаема всё это время**, я её сам ломал
ограничениями оркестратора.

## 3. Что доказано сегодня локально (4 июня вечер)

Артём дал команду на полный регресс всех 6 сцен через **параллельные субагенты
прямо из этой сессии Claude Code** (не открывая 5 вкладок руками — это его был
запрос «давай через subagents в одной сессии»).

**Запуск**: 5 параллельных `Agent`-вызовов в одном assistant-сообщении. Каждый
субагент получил:
- Контракт своей сцены из storyboard.
- **Абсолютные пути** к `reference_pack.md`, `index.html`, `fonts/` — НЕ относительные.
- Явный запрет на debug-метки, lint-loop, чтение SKILL.md.

**Метрики**:
| Параметр | Server headless (наш текущий код) | Параллельные subagents (сегодня) |
|---|---|---|
| Wall-clock на 5 сцен | 30-60 мин (часть фейлит) | **2:10** |
| Turns на сцену | 8 (max-cap), часто не дотягивает до Write | 2-3 (абс. пути убирают разведку) |
| Write success rate | 60-80% | **100%** |
| Финальное качество | Валидно если выживет | Валидно (проверено `_scene_valid_minimal`) |

**Это в 15-20× быстрее текущего production-кода.** При этом нагрузка на подписку
Артёма — по его оценке 3-5% дневного лимита за весь день (5 субагентов + 4 в
fix-round + рендер 6×30fps×5s через локальный puppeteer + аудит).

**Дополнительный фикс рендера**: при рендере scene_04 puppeteer-ом мы поймали
что `tl.seek(t)` для tween-of-plain-object (GSAP `gsap.to({v:0}, {v:100, onUpdate:...})`)
**не вызывает `onUpdate`**, и DOM остаётся в начальном состоянии. **Фикс**: заменить
на `tl.progress(t / tl.duration())`. Это глобально для всех HF-сцен с onUpdate-зависимостями.

Эту бажку **`npx hyperframes render` тоже скорее всего имеет** (мы не проверяли,
но логика та же), и в чёрном ящике npx мы бы её не поймали.

## 4. План Фазы 1 (production-минимум, что предлагаю писать)

Цель: B-roll генерится в боте за **2-3 минуты** вместо 40, с тем же качеством
которое мы доказали сегодня в reel.mp4.

### Изменение 1: параллельный per-scene build

Текущий код (`hyperframes_broll.py`, упрощённо):
```python
def _run_build_phase(storyboard: dict) -> float:
    cost = 0.0
    done = []
    _clear_scene_files()
    for i, scene_file in enumerate(SCENE_FILES, start=1):
        scene_id = f"scene_{i:02d}"
        for attempt in range(1, MAX_SCENE_BUILD_ATTEMPTS + 1):
            try:
                cost += _run_claude(
                    _build_scene_prompt(storyboard, scene_id, done),
                    timeout=SCENE_BUILD_TIMEOUT,
                    tools="Read,Edit,Write,Glob,Grep",   # Bash запрещён
                    max_turns=8,                          # слишком мало
                )
                # ... validate, retry на timeout/невалид
```

Предлагаю:
```python
def _run_build_phase(storyboard: dict) -> float:
    _clear_scene_files()
    # Параллельно через ThreadPoolExecutor (или asyncio + subprocess_exec).
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_build_one_scene, storyboard, scene_id, scene_file): scene_id
            for i, scene_file in enumerate(SCENE_FILES, start=1)
            for scene_id in [f"scene_{i:02d}"]
        }
        results = [f.result() for f in as_completed(futures)]
    # сразу валидация каждой через _scene_valid_minimal
    # если есть нарушения — второй параллельный батч fix-агентов с координатами
    return sum(r.cost for r in results)

def _build_one_scene(storyboard, scene_id, scene_file) -> SceneResult:
    cost = 0.0
    for attempt in range(1, MAX_SCENE_BUILD_ATTEMPTS + 1):
        try:
            cost += _run_claude(
                _build_scene_prompt(storyboard, scene_id, done=[]),  # done=[] для парал.
                timeout=SCENE_BUILD_TIMEOUT,
                tools="Read,Edit,Write,Glob,Grep,Bash",   # Bash вернуть
                max_turns=16,                              # с запасом
            )
            if _scene_valid_minimal(HF_PROJECT / scene_file, scene_id)[0]:
                return SceneResult(scene_id, cost, ok=True)
        except HyperFramesTimeout as e:
            # rate-limit-aware: если note есть — НЕ retry, fail-fast
            ...
    return SceneResult(scene_id, cost, ok=False)
```

**Trade-off параллельности**: теряем `done_scenes` (передача готовых сцен для
единства стиля). Митигация: storyboard уже фиксирует `visual_style + brand-цвета`,
+ `reference_pack.md` единый — стилевое единство держится через контракт.

### Изменение 2: промпт сцены — абсолютные пути + чистка debug-меток

Сегодняшний `_build_scene_prompt` говорит «прочитай `reference_pack.md` и
`index.html`» — относительные. На сервере CWD=`HF_PROJECT` это работает, но
Claude всё равно тратит turn на `Read reference_pack.md` без проверки существования.

Делаю в промпте: **абсолютные пути**:
```
ПРОЧТИ РОВНО ДВА файла (абсолютные пути):
- /home/maksim-bot/hyperframes-broll/reference_pack.md
- /home/maksim-bot/hyperframes-broll/index.html
```

И **явный запрет debug-меток**:
```
НЕ добавляй в финальный HTML:
- метки «SCENE 01», «SCENE / NN», «FINAL CTA», «DEBUG» и подобные —
  это служебные пометки разработчика, в финальном видео их быть не должно.
- любые комментарии «TODO», «FIXME».
```

### Изменение 3: render через puppeteer + ffmpeg (свой)

Сейчас: `npx --yes hyperframes@0.6.56 render -c scene_NN.html -o hf_NN.mp4`.
Минусы: чёрный ящик seek/progress, npx-cache cold start = минуты, зависимость от
сетевого fetch.

Предлагаю: `tools/render_scene.mjs` (как сегодня локально) — puppeteer-core +
встроенный chromium → 150 PNG-кадров → ffmpeg encode h264 yuv420p crf 20.

Замеры локально: **26 секунд на сцену**, 6 сцен последовательно ≈ 2:50. Или
параллельно (на сервере с достаточной RAM) — теоретически быстрее, но 6 puppeteer
instances одновременно = ~3GB RAM, надо мерить.

### Изменение 4: motion smoke-test как gate

После build-фазы, перед render: для каждой сцены `tools/motion_smoketest.mjs`
снимает 3 кадра (t=0.5, 2.5, 4.5) и сравнивает MD5-хэши. Если все 3 идентичны —
анимация сломана (timeline registered but not progressing). Fail-fast.

Сегодня это бы поймало scene_04 со «застрявшим counter'ом 0%» **до** того как мы
потратили время на ffmpeg.

### Изменение 5: rate-limit awareness уже частично есть

`_parse_stream → rate_limit_info → _rate_limit_note` уже работает (закоммичено
4 июня). Дополняю:
- **Pre-flight probe** перед запуском параллельного батча: если
  `utilization > 0.7` — гнать по 2-3 параллельно. Если > 0.9 — ждать reset с
  информативным сообщением пользователю.
- Backoff при `rejected` mid-batch.

## 5. Альтернативы которые отверг (с обоснованием)

### Отдельная Max-подписка $100/мес для бота — НЕТ
Изначально я предложил «отдельную подписку чтобы снять шаринг с твоим dev-токеном».
Артём указал что это **conflict of interest** — я Claude от Anthropic, и в роли
CTO предлагать клиенту больше платить Anthropic — нечестно. Реальный расчёт: за
весь сегодняшний день (5 субагентов + 4 fix + рендер + аудит) использовано ~3-5%
от Max-окна. Бот в проде не делает такой объём.

Правильное решение: **rate-limit awareness в коде** (Изменение 5) + умное
расписание батчей. Это бесплатно и решает шаринг архитектурно.

### Template + JSON-only — отложено в Фазу 2
Когда-то нам предложили: 12 рукописных HTML-шаблонов (один на архетип) + LLM
возвращает **только данные** (числа, тексты, тайминги) через structured output.
Python подставляет → 30 сек на сцену вместо 70-90.

Не делаю в Фазе 1 потому что:
- Initial investment: 12 шаблонов × 2-3 часа = 30-40 часов работы.
- Сегодняшние сцены становятся **эталонами** для шаблонов — без них шаблоны
  будут хуже того что мы видим сейчас.
- Параллель + abs paths + bash + max_turns=16 уже даёт **2-3 минуты на B-roll**.
  Это уже UX-приемлемо для production. Template даст оптимизацию до 30-60 сек —
  это уже next-tier.

Делать после того как Фаза 1 стабилизируется в проде, накопится статистика
реальной скорости и стоимости, и появится явная мотивация ещё ускорять.

### Anthropic Batch API — отложено
Альтернатива параллельному subprocess.run — отправить 6 сцен одним Batch-вызовом
Anthropic API. Плюс: 50% дешевле, не считается в interactive rate-limit. Минус:
async, до 24h SLA на batch (хотя обычно минуты), и архитектурно другой паттерн
(не Claude Code CLI, а прямой Anthropic SDK с tool calling).

Делать после Фазы 1, когда увидим реальные счета и поведение rate-limit на
параллельных subprocess. Если упрёмся — Batch это естественный следующий шаг.

## 6. Открытые вопросы — где я не уверен, прошу адверсариально оценить

1. **Параллельный subprocess.run × 6 одновременно** — что я упускаю?
   - Memory footprint: 6 одновременных `claude` процесса × сколько RAM?
     Я не замерял. Сервер 7.6Gi total, обычно занято ~1.3Gi. Если каждый
     `claude` берёт ~500MB-1GB — упрёмся в OOM.
   - Race conditions: 6 процессов пишут в одну папку `HF_PROJECT`, **разные**
     `scene_NN.html`. Конфликт только если случайно один процесс перепишет
     чужой scene. Митигация — абс. пути и явный scene_id в промпте.
   - **`_revert_stray` в `_run_build_phase`**: сейчас он откатывает посторонние
     файлы. При параллели **6 процессов могут конкурентно вызывать git** — это
     явно сломается. Нужен сериализованный финальный revert.

2. **Делать ли done_scenes-context при параллельности?**
   - В sequential-варианте каждая последующая сцена получала список готовых
     («не повторяй приёмы»). В параллельном варианте этого нет.
   - Гипотеза: storyboard-валидатор уже гарантирует ≥5 уникальных архетипов +
     non-adjacent same archetype. То есть **разнообразие задано на старте**, не
     требует cross-talk сцен.
   - Риск: визуальный стиль (цвета вне brand, типография) может разойтись.
     Митигация: всё в `reference_pack.md` (brand colors inline в промпте).
   - Прошу оценить достаточно ли этого, или нужен какой-то sync.

3. **Render: 6 puppeteer параллельно vs 6 последовательно**
   - Локально сегодня делал 6 последовательно (~3 мин total). На сервере с
     8Gi RAM 6 puppeteer = вероятно OOM. Если делать по 2-3 параллельно — какая
     политика? Просто `Pool(3)`?
   - Альтернатива — рендер через `npx hyperframes render` сохранить, а seek→progress
     bug проверить тестом. Если они уже используют progress() — наш самосбор не
     нужен.
   - Прошу оценить ROI «свой puppeteer vs `npx hyperframes render`» с точки
     зрения maintenance + reliability на live сервере, не на ноутбуке.

4. **Motion smoke-test — не слишком строгий?**
   - 3 кадра 0.5/2.5/4.5. Что если timeline 0-5s проигрывает анимацию только в
     **первой секунде** (intro), а потом — ambient hold? Тогда t=2.5 и t=4.5
     идентичны → false-negative motion.
   - Митигация: дополнить t=1.0. Или анализировать pixel-diff не только
     full-frame hash, а конкретно по area-of-interest.
   - Прошу оценить.

5. **Что я не вижу в плане в принципе** — главный вопрос. После 3 дней
   tunnel-vision я мог упустить целые классы проблем. Что бы ты как сторонний
   reviewer добавил/убрал/перестроил в плане?

## 7. Что НЕ предмет ревью (зафиксировано)

- Архитектура storyboard → per-scene → render → assembler — work-proven.
- Layout-детектор (`tools/hf_inspect_layout.mjs`) — work-proven.
- `_scene_valid_minimal` с CDN-whitelist — work-proven.
- Storyboard-валидатор разнообразия — work-proven.
- 50/50 split layout с `brand_name="maksim"` (crop_y=120) — work-proven.

Эти куски трогать не планирую, ревью на них не нужно.

## 8. Что я прошу от тебя

≤ 600 слов адверсариального ответа с:
1. Главные дыры в плане Фазы 1 (что сломается на проде).
2. Топ-3 что бы ты добавил/изменил **до** начала имплементации.
3. Если есть архитектурное решение **лучше** параллельного subprocess.run × 6 —
   назови (Batch API? Anthropic SDK напрямую? `asyncio.create_subprocess_exec`
   instead of ThreadPoolExecutor?).
4. Оценка реалистичности 2-3 минут на полный B-roll в production условиях
   (один пользователь, шаренная подписка). 5? 10?
5. Конкретные open questions (раздел 6) — твои ответы.

Не нужно: похвалы, повторения известного, общих рекомендаций «думайте о пользователе».
