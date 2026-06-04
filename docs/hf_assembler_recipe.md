# HyperFrames → финальный ролик: параметры video_assembler

Рецепт сборки 30-сек 9:16 ролика из 6 HyperFrames-вставок + аватара Максима.
Подтверждённые в коде параметры (`video_assembler.py`, проверены 4 июня 2026).

## 1. Структура папки проекта

```
<project_dir>/
├── avatar_*.mp4              # аватар Максима (HeyGen), 9:16, ≥avatar_duration
├── hyperframes/
│   ├── hf_01.mp4             # ← кладёт hyperframes_broll._render_all
│   ├── hf_02.mp4
│   ├── ...
│   └── hf_06.mp4
└── _tmp_montage/             # создаётся ассемблером, удаляется в конце
```

`_render_all(out_dir)` сам создаёт `out_dir/hyperframes/`. Аватар приносишь
отдельно (HeyGen-render по сценарию + голосу — поверх это всё уже работает в
bot.py через handle_callback).

## 2. Вызов сборки

```python
from pathlib import Path
from video_assembler import assemble_auto_montage

final = assemble_auto_montage(
    project_dir = Path("/tmp/hf_render_1717000000"),
    layout      = "split",      # ← 50/50 top-bottom (HF сверху, аватар снизу)
    broll_mode  = "hf",         # ← только hyperframes/hf_*.mp4 (НЕ remotion, НЕ smm)
    brand_name  = "maksim",     # ← _AVATAR_CROP_Y_BY_BRAND["maksim"]=120 (голова не режется)
    subtitles   = False,        # True → CapCut-сабы (whisper-транскрипция, медленно)
)
print(final)  # → <project_dir>/final_auto.mp4 (или final_auto_subs.mp4)
```

Все 4 параметра обязательные для нашего случая:
- `layout="split"` — единственный вариант, который оставляет говорящую голову
  Максима ВИДНОЙ под графикой. `dynamic` / `pro` перекрывают аватар (для контент-
  бота Артёма норм, для Максима неподходит — он на брендинг себя ставит).
- `broll_mode="hf"` — namespace разделён в `_find_broll`. Если поставить `"mix"`,
  попадут и остатки Remotion из `autobroll/`, конфликт.
- `brand_name="maksim"` — критично. У Максима голова выше центра кадра, дефолтный
  `DEFAULT_AVATAR_CROP_Y=280` режет лоб. `maksim=120` доказан 30 мая (коммит
  `9df996e fix(montage): per-brand avatar crop_y`).
- `subtitles=False` — на первом прогоне без сабов (whisper +20-40с к рендеру).
  Включать когда визуально финал ок.

## 3. Что НЕ передавать

- `montage_plan` — только для `layout="pro"`, не наш случай.
- `smart_mix_cfg` — только для `layout="smart"`.
- `subtitle_language` — дефолт "ru" уже верный.

## 4. Длительности

Ассемблер режет 6 HF-клипов по `avatar_duration` пропорционально. Если аватар
30с + 6 клипов — каждый получит ~5с экранного времени (что совпадает с
`data-duration="5"` в композициях, контракт держится).

## 5. Полный путь от storyboard до final_auto.mp4

```bash
# Шаг 1: build + render через скилл (на сервере, ~15-25 мин при свежем rate-limit)
cd /home/maksim-bot/maksim-bot && \
  sudo -u maksim-bot env CLAUDE_CODE_OAUTH_TOKEN=... HOME=/home/maksim-bot \
  venv/bin/python tools/run_buildphase_only.py

# Шаг 2: рендер 6 HTML → MP4 (npx hyperframes render × 6, минуты)
sudo -u maksim-bot env HOME=/home/maksim-bot \
  venv/bin/python tools/render_only.py /tmp/hf_render_test

# Шаг 3: положить avatar_*.mp4 в /tmp/hf_render_test/ (от HeyGen)

# Шаг 4: сборка
sudo -u maksim-bot env HOME=/home/maksim-bot venv/bin/python -c "
from pathlib import Path
from video_assembler import assemble_auto_montage
print(assemble_auto_montage(
    Path('/tmp/hf_render_test'),
    layout='split', broll_mode='hf', brand_name='maksim'))
"
```

## 6. Контракт ассемблера НА что опирается рендерер HF

- `_render` пишет MP4 через `npx hyperframes render -c scene_NN.html -o ...mp4`.
  Выход 1080×1920, 30fps, h264. `_find_broll` (assembler) принимает любой mp4 в
  `hyperframes/`, аспект-чек делает `_probe_aspect`.
- Имена `hf_NN.mp4` сортируются numerically (см. `_sort_key` в video_assembler).
  6 сцен → порядок 01..06.

## Известные грабли

- Без `HYPERFRAMES_BROWSER_PATH` → может подхватиться `snap-chromium` → краш
  рендера. `_render_env()` это проставляет, но если запускаешь руками — проверь.
- Без `HOME=/home/maksim-bot` → npx идёт в `/root/.npm`, права валятся.
- Если в `hyperframes/` остались старые `hf_*.mp4` от прошлых прогонов —
  ассемблер их подхватит. Перед новым прогоном чистить папку или менять
  `project_dir`.
