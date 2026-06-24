# Bootstrap ворктри-дерева (для параллельной сессии)

> Runbook: поднять окружение в свежем git-worktree, чтобы в нём шли тесты, рендер
> и деплой. Запускать В САМОМ дереве. Конкретный пример — дерево Илона
> `D:\AI\maksim-bot-panferov` (ветка `work/panferov`). Модель целиком:
> `docs/PARALLEL_SESSIONS_WORKTREE.md`.

## Что git НЕ переносит в новое дерево (отсюда и bootstrap)

- **`node_modules/`** — gitignored, per-dir. Нужен `npm install` (ставит
  puppeteer / pixelmatch / pngjs — это карусель и скриншот-рендер).
- **`.env`** — gitignored, секреты. Нужен ТОЛЬКО для локального запуска бота /
  полного пайплайна. Для тестов и деплоя — не нужен.
- **Python — venv НЕ нужен.** Рабочее дерево Нолана (`D:\AI\maksim-bot`) тоже на
  **глобальном Python 3.12**, депы стоят глобально → новое дерево использует тот
  же глобальный python (сверено 24.06). venv заводи только если намеренно
  изолируешь зависимости — для одного ядра с общим `requirements.txt` не требуется.

## Шаги (в PowerShell, из дерева Илона)

```powershell
cd D:\AI\maksim-bot-panferov

# 1. Python-депы (глобальные) — проверить, что на месте. venv НЕ создаём.
python -c "import telegram, anthropic, pytest; print('py deps OK')"
#    если что-то не импортится → pip install -r requirements.txt   (глобально)

# 2. node_modules — единственное, чего реально нет в свежем дереве:
npm install
#    Node 24 / npm 11; ~пара минут. Ставит puppeteer/pixelmatch/pngjs.

# 3. .env — ТОЛЬКО если гоняешь бота/полный пайплайн локально:
#    положи СВОЙ panferov .env. НЕ копировать .env из D:\AI\maksim-bot —
#    там тенант maksim (чужие токены/ключи). Для тестов и деплоя .env не нужен.
```

⚠️ **НЕ симлинкать `node_modules` между деревьями** — на Windows npm плодит
junctions, Node-резолв спотыкается, и ветки могут разойтись по версиям пакетов.
Ставить отдельной установкой в каждом дереве.

## Проверки (всё зелёное → дерево готово)

```powershell
# A. Страж бренд-протечки в ядро (новый — ловит хардкод палитры в коде):
python -m pytest tests/test_core_no_brand_leak.py -q
#    ожидаем: 2 passed.

# B. Per-tenant резолв стиля panferov (бело-синий #2E9BE0, без оранжевого
#    Максима #FF5722):
python tests/test_style_contract_panferov.py
#    ожидаем: PASS.

# C. node_modules встал (карусель/скриншот-рендер):
node -e "require('puppeteer'); console.log('puppeteer OK')"

# D. (опц., если ставил .env) импорт всего бота:
python -c "import bot; print('import OK')"
#    может требовать .env-переменные — это норм (см. шаг 3).
```

Прогон A+B+C достаточно, чтобы считать дерево готовым к работе и деплою. D — для
тех, кто будет гонять бота локально.

## Деплой из этого дерева

Уже настроено в скилле `deploy-content-bot` (source = `D:\AI\maksim-bot-panferov`,
target `/root/contentbot-core/`, рестарт `contentbot-core.service`). Гейт «только
закоммиченное» — Шаг 1.5. Для самого деплоя venv/node локально НЕ нужны (это git +
scp); `node_modules` — только для локального рендера/тестов карусели.

## Дальше — дневной флоу (из `PARALLEL_SESSIONS_WORKTREE.md`)

- Раз в день: `git fetch && git merge origin/maksim-prod` в `work/panferov` —
  подтянуть свежее ядро (улучшения Нолана доезжают до тебя). Конфликты редки —
  ветки короткие.
- Стиль panferov правишь ТОЛЬКО в `style_contract.panferov.json`, НЕ в коде ядра
  (страж A это и сторожит — хардкод цвета в `hyperframes_broll.py` и пр. = fail).
- Деплоишь ТОЛЬКО из своего дерева и ТОЛЬКО закоммиченное (иначе кросс-контаминация
  с деревом Нолана).
