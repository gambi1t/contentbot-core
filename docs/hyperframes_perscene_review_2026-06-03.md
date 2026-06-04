# Внешнее ревью: per-scene build HyperFrames не укладывается в таймаут

> Документ для независимого эксперта (ChatGPT). Параллельно тот же код смотрит
> субагент Claude. Цель: найти изъяны подхода/кода, объясняющие почему генерация
> ОДНОЙ видео-сцены через Claude Code CLI то укладывается в 5 минут, то не
> укладывается в 10, и как сделать это надёжным.

## Что мы строим
Telegram-бот автономно генерит 6 графических B-roll-вставок (вертикальные
1080×1920 HTML/GSAP-композиции через фреймворк HyperFrames) под 30-сек сценарий,
рендерит в MP4, монтирует поверх AI-аватара.

Архитектура (после долгой эволюции, синтез прошлого Deep Research + ChatGPT):
- **Фаза 1 (storyboard):** `claude -p` (headless, на сервере, Max-подписка) пишет
  `storyboard.json` — 6 сцен с РАЗНЫМИ архетипами (business_archetype +
  hf_technique + visual_style + motion_family). Машинный валидатор гейтит
  разнообразие. Работает отлично (~3 turns, валиден с 1й попытки).
- **Фаза 2 (per-scene build):** на КАЖДУЮ сцену отдельный `claude -p` пишет
  `scene_NN.html` по её контракту из storyboard. ← ВОТ ЗДЕСЬ ПРОБЛЕМА.

## ПРОБЛЕМА (главное)
Последний реальный прогон (3 июня):
- scene_01 (`before_after_cards`): 1 таймаут (10 мин) → retry → **готов** (14 KB,
  качественный, детектор 0 issues).
- scene_02 (`cashflow_timeline`): **3 таймаута по 10 мин = 30 мин впустую, ни разу
  не записал файл → fail**, весь ролик не собрался.

При этом **диагностика ОДНОЙ сцены за 8 минут до прогона уложилась в 4:35**
(успех, scene записана, детектор чисто). То есть скорость Anthropic API/Claude
Code **сильно гуляет в пределах часа** (то 4 мин, то >10). Диагностика поймала
быстрое окно, полный прогон попал в медленное.

**Слепое пятно:** stream-json парсится ВНУТРИ `_run_claude`, в лог пишется только
итог. Мы НЕ видим, что Claude делал 10 минут в упавших попытках scene_02 —
зациклился (lint-loop?) или просто медленный API. Это первое что чиним (дамп
stream), но хотим внешний взгляд на подход в целом ДО этого.

## Наблюдения из stream успешной сцены (диагностика 4:35)
Tools: Read×5 (reference_pack.md, SKILL.md, design.md, index.html) + Bash (ls
fonts) + **Write×1** + потом **Edit×2 (подгонка safe-area) + Bash lint×2**.
То есть Claude ПОСЛЕ Write делает self-проверки (lint, правки), несмотря на
явное правило в промпте «сразу заверши, без lint». turns=12, cost $1.08.

## Вопросы к эксперту
1. **Почему scene_02 за 3×10 мин ни разу не дошёл до Write, а scene_01 дошёл?**
   Чистый transient, или подход провоцирует медленноту? Что в коде/промпте
   способствует?
2. **Не слишком ли много работы на сцену?** Каждая сцена читает reference_pack.md
   (~200 строк) + SKILL.md (490 строк) + design.md + index.html заново. Стоит ли
   убрать чтение SKILL.md (reference_pack — уже выжимка из него)? Inline-ить
   reference_pack прямо в промпт, чтобы не было Read? Сократит ли это время?
3. **Архитектурно: 3 таймаута × 10 мин = 30 мин на одну сцену — это тупик.** Как
   правильно строить надёжную headless-генерацию при нестабильной скорости LLM?
   Параллелить сцены? Меньше таймаут + больше попыток? Глобальный дедлайн на весь
   ролик с graceful-degradation (отдать что успели)?
4. **Claude игнорит «сразу заверши, без lint»** и делает self-проверки. Бороться
   (жёстче запрет, убрать Bash из allowedTools) или принять (Edit-подгонка
   safe-area улучшает качество)? Сейчас allowedTools = Read,Edit,Write,Glob,Grep
   (Bash НЕ в списке — но Claude всё равно делает Bash lint?? возможно встроенный).
5. **Сам подход «отдельный claude -p на сцену»** — правильный для headless
   production, или есть лучше (один долгий claude с TodoList на 6 сцен? batch?
   программная сборка сцен из шаблонов + LLM только для текста)?

## КОД (актуальный)

### Фаза 2 — per-scene цикл (`_run_build_phase`)
```python
SCENE_BUILD_TIMEOUT = 600    # сек на сцену
MAX_SCENE_BUILD_ATTEMPTS = 3 # генерация + 2 retry

def _run_build_phase(storyboard: dict) -> float:
    cost = 0.0
    done = []
    _clear_scene_files()  # удалить старые scene_NN.html (защита от ложного _scene_done)
    for i, scene_file in enumerate(SCENE_FILES, start=1):
        scene_id = f"scene_{i:02d}"
        sc = _scene_contract(storyboard, scene_id)
        written = False
        for attempt in range(1, MAX_SCENE_BUILD_ATTEMPTS + 1):
            try:
                cost += _run_claude(_build_scene_prompt(storyboard, scene_id, done),
                                    timeout=SCENE_BUILD_TIMEOUT)
            except HyperFramesTimeout:
                # Claude часто пишет HTML, но не завершается в срок → timeout,
                # хотя файл готов. Проверяем файл — если записан, ПРИНИМАЕМ.
                _revert_stray()
                if _scene_done(scene_file):
                    written = True; break
                # файла нет → retry (скорость гуляет, повтор может попасть в быстрое окно)
                continue
            _revert_stray()
            if _scene_done(scene_file):
                written = True; break
        if not written:
            raise HyperFramesBrollError(f"{scene_id} не сгенерирован за {MAX_SCENE_BUILD_ATTEMPTS} попыток")
        done.append({"id": scene_id, "archetype": sc.get("business_archetype"),
                     "primary_text": sc.get("primary_text")})
    return cost

def _scene_done(scene_file):  # сцена записана = файл >200 байт
    p = HF_PROJECT / scene_file
    return p.exists() and p.stat().st_size > 200
```

### `_run_claude` (запуск + stream-json парсинг)
```python
def _run_claude(prompt, timeout=None):
    _timeout = timeout if timeout is not None else CLAUDE_TIMEOUT
    env = ...  # CLAUDE_CODE_OAUTH_TOKEN (Max-подписка), убираем ANTHROPIC_API_KEY
    for attempt in range(1, _MAX_CLAUDE_ATTEMPTS + 1):  # _MAX=2 (SIGTERM-retry)
        try:
            proc = subprocess.run(
                ["claude", "-p", prompt,
                 "--allowedTools", "Read,Edit,Write,Glob,Grep",
                 "--output-format", "stream-json", "--verbose"],
                cwd=HF_PROJECT, env=env,
                capture_output=True, text=True, timeout=_timeout)
        except subprocess.TimeoutExpired:
            # raise HyperFramesTimeout (НЕ повторяем здесь — partial stdout ТЕРЯЕТСЯ)
            raise HyperFramesTimeout(f"не уложился за {_timeout//60} мин")
        ...
    # парсим proc.stdout построчно (JSONL), берём type=result → total_cost_usd
```

### Промпт сцены (`_build_scene_prompt`) — сокращённо
```
Ты — моушн-дизайнер HyperFrames. АВТОНОМНЫЙ режим: не задавай вопросов.
Создай РОВНО ОДНУ композицию scene_NN.html по контракту из раскадровки.
НЕ трогай другие scene-файлы.

КОНТРАКТ СЦЕНЫ: { <JSON одной сцены из storyboard: archetype/technique/style/...> }

[если есть готовые сцены: их список для единства стиля]

ОБЯЗАТЕЛЬНО ПРОЧИТАЙ:
- reference_pack.md (визуальный вокабуляр, motion-правила, анти-паттерны)
- .agents/skills/hyperframes/SKILL.md (правила HyperFrames)
- design.md (бренд), index.html (образец + @font-face)

ПРАВИЛА: реализуй archetype/technique/style; кадр 1080×1920 5сек; @font-face
из index.html; SAFE-AREA x[40,1040] y[480,1440] flex-column; детерминизм
(no Math.random/Date.now/repeat:-1); window.__timelines[id]={paused:true}.

🔴 ВАЖНО: как только записал NN.html — СРАЗУ заверши. НЕ запускай рендер/lint,
не читай обратно, не переписывай. Один Write и стоп.
```

### Контекст: storyboard этого прогона (6 архетипов)
```
scene_01 | before_after_cards   | css_3d_transforms          | swiss_pulse    → ГОТОВ (1 таймаут+retry)
scene_02 | cashflow_timeline    | svg_path_drawing           | shadow_cut     → FAIL (3 таймаута)
scene_03 | formula_card         | kinetic_typography         | data_drift
scene_04 | reserve_gauge        | clip_path_reveal           | velvet_standard
scene_05 | stack_layers         | velocity_matched_transitions | deconstructed
scene_06 | final_cta            | character_typing           | maximalist_type
```
(cashflow_timeline + svg_path_drawing — та же сцена в прошлой сессии писалась за
5 мин. Так что архетип сам по себе не «тяжёлый», дело в окне скорости.)

## Что уже учтено (не советуй заново)
- Max-подписка → кредиты/cost не критерий (retry «бесплатный»).
- Storyboard-гейт разнообразия работает (это НЕ проблема).
- Layout-детектор (puppeteer) ловит overlap/offscreen ПОСЛЕ рендера — отдельно.
- Приём записанного-при-таймауте файла уже реализован (но scene_02 файла НЕ дал).
