"""hyperframes_broll.py — автономная генерация B-roll-вставок через HyperFrames.

ВТОРОЙ движок графического B-roll, параллельный Remotion (`auto_broll.py`).
Тот же контракт: сценарий → 6 готовых MP4-вставок.

Цепочка: сценарий → Claude Code на сервере пишет 6 standalone HTML-композиций
`scene_01.html`…`scene_06.html` (skill /hyperframes + design.md) →
`npx hyperframes render -c scene_NN.html` → hf_01..06.mp4.

Отличия от auto_broll (Remotion):
- движок HyperFrames (HTML+GSAP), не Remotion (React);
- рендер требует HYPERFRAMES_BROWSER_PATH → chrome-headless-shell (на сервере
  системный snap-chromium не работает headless, см. project_maksim_dual_broll_engine.md);
- выход в `out_dir/hyperframes/hf_NN.mp4` (отдельный namespace от autobroll/).

Защита: лок (общий проект), откат лишних правок Клода (git), повтор с
передачей ошибки рендера, жёсткий лимит попыток. Без участия человека.

Standalone-тест:
    python hyperframes_broll.py "_hf_test" < script.txt
"""
from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import storyboard_validator as _sv

logger = logging.getLogger(__name__)

# scene_NN.html — отдельные файлы, но git-проект общий: два параллельных
# прогона затрут правки друг друга. Генерация строго последовательная.
_GEN_LOCK = threading.Lock()

# ── Пути и константы ─────────────────────────────────────────────────
HF_PROJECT = Path(
    os.getenv("HYPERFRAMES_PROJECT_DIR", "/home/maksim-bot/hyperframes-broll")
)
N_INSERTS = 6
SCENE_FILES = [f"scene_{i:02d}.html" for i in range(1, N_INSERTS + 1)]
STORYBOARD_FILE = "storyboard.json"     # фаза 1 пишет сюда (gated валидатором)
REFERENCE_PACK_FILE = "reference_pack.md"  # curated-выжимка скилла (деплоится в HF_PROJECT)
MAX_STORYBOARD_ATTEMPTS = 3             # генерация storyboard + 2 fix-round
SCENE_BUILD_TIMEOUT = 600              # сек на ОДНУ сцену (per-scene build).
                                       # Диагностика 3 июня: scene_01 пишется
                                       # ~5 мин, НО скорость Claude/API гуляет
                                       # (transient) — то 5, то >7 мин. 10 мин
                                       # даёт запас; на таймаут — retry (ниже).
MAX_SCENE_BUILD_ATTEMPTS = 3           # генерация сцены + 2 retry (на transient-таймаут)
CLAUDE_TIMEOUT = 1800     # сек на одну сессию Claude Code (HTML для 6 сцен).
                          # 29 мая фактически уходило ~4 мин; 31 мая упало в
                          # 900 — корень не доказан, поднимаем до 30 мин
                          # как страховка (см. daily/2026-06-01-maksim-bot.md).
RENDER_TIMEOUT = 300      # сек на рендер одной вставки
MAX_FIX_ROUNDS = 2        # сколько раз просим Клода починить
HF_VERSION = "0.6.56"     # пин версии CLI (как в package.json проекта)


class HyperFramesBrollError(Exception):
    """Не удалось сгенерировать B-roll через HyperFrames даже после повторов."""


class HyperFramesTimeout(HyperFramesBrollError):
    """Claude Code не уложился в CLAUDE_TIMEOUT (по умолчанию 900s = 15 мин).
    Не transient — retry бесполезен, та же ситуация повторится.

    Артём 31 мая 2026: сырой `subprocess.TimeoutExpired` пробрасывался
    в bot.py и через `str(e)` юзер видел всю команду с промптом
    (~4 KB) в Telegram. Класс введён чтобы card_hfbroll handler
    различал случай и показывал понятное сообщение + кнопки повтора
    или переключения на Remotion.
    """


class HyperFramesInterrupted(HyperFramesBrollError):
    """Claude Code был ПРЕРВАН внешним сигналом (SIGTERM/SIGKILL) — обычно
    это systemd-restart maksim-bot во время рендера или OOM-kill. Не баг
    Claude и не баг скилла — инфра-событие. Юзеру надо показать «попробуй
    ещё раз», а не пугающее «упал».

    Артём 31 мая 2026: после systemd-restart maksim-bot в момент рендера
    HyperFrames пользователь видел «⚠️ Claude Code упал (rc=143)». Класс
    введён чтобы card_hfbroll handler различал такие случаи и предлагал
    retry-кнопку.

    Наследник HyperFramesBrollError — catch-all `except HyperFramesBrollError`
    всё ещё ловит, обратная совместимость не нарушена.
    """


# Returncodes, которые означают «процесс убит снаружи», а не упал по своей
# ошибке. POSIX: 128+signal (143=SIGTERM, 137=SIGKILL). Python subprocess
# при terminate() возвращает signed (-15=SIGTERM, -9=SIGKILL).
_INTERRUPT_RC = {143, -15, 137, -9}

# Авто-retry: если subprocess `claude` получил SIGTERM — ждём
# HF_RETRY_DELAY_SEC и пробуем ещё раз ОДНОЙ попытки. Контекст
# (см. reference_claude_code_server_subscription.md): CLI на сервере
# живёт через Max-подписку, кредиты НЕ критерий, retry «бесплатный».
# Узкий, но реальный кейс — OOM-killer убил конкретно subprocess
# (а не bot.py) либо ручной kill. Если bot.py жив — успеет повторить
# и юзер вообще не увидит ошибку.
HF_RETRY_DELAY_SEC = float(os.getenv("HF_RETRY_DELAY_SEC", "3"))
_MAX_CLAUDE_ATTEMPTS = 2  # 1 первая + 1 retry на SIGTERM


# ── Поиск chrome-headless-shell ──────────────────────────────────────
def _resolve_browser_path() -> str | None:
    """Путь к chrome-headless-shell (НЕ системный snap-chromium).

    Версия в пути меняется при переустановке — глобим, не хардкодим.
    Приоритет: явный HYPERFRAMES_BROWSER_PATH из env → глоб в проекте.
    """
    explicit = os.getenv("HYPERFRAMES_BROWSER_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    pattern = str(
        HF_PROJECT / "chrome-headless-shell" / "*" /
        "chrome-headless-shell-linux64" / "chrome-headless-shell"
    )
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


# ── Anthropic-ключ (фолбэк, если нет подписки) ───────────────────────
def _anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        env = Path(__file__).parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key:
        raise HyperFramesBrollError("Нет ANTHROPIC_API_KEY (env или .env).")
    return key


# ── Промпт для Claude Code ───────────────────────────────────────────
# ── ФАЗА 1: storyboard (machine-gated diversity) ─────────────────────────
def _read_storyboard() -> dict | None:
    """Читает storyboard.json из HF_PROJECT. None если нет/не парсится."""
    p = HF_PROJECT / STORYBOARD_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[hf_broll] storyboard.json не распарсился: {e}")
        return None


def _build_storyboard_prompt(script_text: str) -> str:
    """Промпт фазы 1: Claude планирует 6 РАЗНЫХ сцен → storyboard.json.
    БЕЗ HTML. Автономно (обход approval-gate скилла)."""
    archetypes = ", ".join(sorted(_sv.BUSINESS_ARCHETYPES))
    motions = ", ".join(sorted(_sv.MOTION_FAMILIES))
    styles = ", ".join(sorted(_sv.VISUAL_STYLES))
    return f"""Ты — арт-директор автономного production-пайплайна HyperFrames.
Это АВТОНОМНЫЙ режим (AUTO_APPROVE): не жди подтверждения пользователя, НЕ
используй AskUserQuestion и никаких интерактивных вопросов — выполни задачу
до конца сам.

ЗАДАЧА ФАЗЫ 1: НЕ пиши HTML. Составь раскадровку 6 графических B-roll-вставок
под сценарий и запиши её в файл `storyboard.json` в корне проекта.

ОБЯЗАТЕЛЬНО прочитай `reference_pack.md` (в корне проекта) — там визуальный
вокабуляр, архетипы, анти-паттерны, правила разнообразия.

СЦЕНАРИЙ (озвучка ~30 секунд):
─────────────────────────────────────
{script_text}
─────────────────────────────────────

ФОРМАТ storyboard.json:
{{
  "version": "1.0",
  "scenes": [
    {{
      "id": "scene_01",
      "script_beat": "<фрагмент сценария, который иллюстрирует эта сцена, ≥20 симв>",
      "business_archetype": "<один из: {archetypes}>",
      "hf_technique": "<техника реализации, напр. svg_path_drawing/kinetic_typography/counter_animation>",
      "visual_style": "<один из: {styles}>",
      "motion_family": "<один из: {motions}>",
      "density": "<sparse | balanced | dense>",
      "scale_profile": "<hero | medium | compact>",
      "primary_text": "<главный текст на экране, 3..80 симв>",
      "reason": "<почему этот архетип лучше всего иллюстрирует момент, ≥20 симв>"
    }},
    ... ещё 5 сцен (scene_02..scene_06)
  ]
}}

ПРАВИЛА РАЗНООБРАЗИЯ (иначе раскадровка не пройдёт валидацию):
- Ровно 6 сцен, id по порядку scene_01..scene_06.
- Соседние сцены НЕ повторяют business_archetype.
- ≥5 уникальных business_archetype из 6 (борьба с монотонностью!).
- ≥4 уникальных motion_family, ≥2 разных density, ≥2 разных scale_profile.
- Архетипы-графики (cashflow_timeline, table_snapshot, calendar_grid) — ≤2 суммарно.
- НЕ 3 сцены подряд с одинаковой density/scale_profile (визуальный ритм).
- scene_06 = final_cta (финальный призыв/итог).
- Каждый business_archetype выбирай ПОД смысл фрагмента сценария.

После записи storyboard.json — закончи. HTML напишешь в следующей фазе."""


def _build_storyboard_fix_prompt(errors: list[str]) -> str:
    """Фаза 1 fix-round: storyboard не прошёл валидацию."""
    return (
        _sv.format_errors_for_claude(errors)
        + "\n\nПерезапиши `storyboard.json` так, чтобы все правила выполнялись. "
        "Не пиши HTML. Не задавай вопросов — это автономный режим."
    )


def _run_storyboard_phase(script_text: str) -> tuple[dict, float]:
    """Фаза 1: Claude пишет storyboard.json → валидация → fix-rounds.
    Возвращает (валидный storyboard, cost). Бросает HyperFramesBrollError,
    если за MAX_STORYBOARD_ATTEMPTS попыток не удалось получить валидный.
    """
    cost = 0.0
    prompt = _build_storyboard_prompt(script_text)
    errors: list[str] = []
    for attempt in range(1, MAX_STORYBOARD_ATTEMPTS + 1):
        cost += _run_claude(prompt)
        sb = _read_storyboard()
        if sb is None:
            errors = ["storyboard.json не создан или не является валидным JSON"]
        else:
            ok, errors = _sv.validate_storyboard(sb)
            if ok:
                n_arch = len({s.get("business_archetype") for s in sb["scenes"]})
                logger.info(
                    f"[hf_broll] storyboard валиден (попытка {attempt}, "
                    f"{n_arch} уникальных архетипов)"
                )
                return sb, cost
        logger.warning(
            f"[hf_broll] storyboard невалиден (попытка {attempt}/"
            f"{MAX_STORYBOARD_ATTEMPTS}): {len(errors)} нарушений"
        )
        prompt = _build_storyboard_fix_prompt(errors)
    raise HyperFramesBrollError(
        f"Раскадровка (storyboard) не прошла валидацию за "
        f"{MAX_STORYBOARD_ATTEMPTS} попыток. Нарушения: {'; '.join(errors[:3])}"
    )


# ── ФАЗА 2: per-scene build (каждая сцена отдельным вызовом) ──────────────
def _scene_done(scene_file: str) -> bool:
    """Сцена записана = файл существует и непустой (>200 байт html)."""
    p = HF_PROJECT / scene_file
    return p.exists() and p.stat().st_size > 200


def _scene_valid_minimal(path, scene_id: str) -> tuple[bool, list[str]]:
    """Строгая (но дешёвая, без рендера) проверка готовой сцены. Заменяет
    наивный >200 байт: ловит обрезанный/каркасный html ДО того, как он попадёт
    в рендер и сожрёт минуты. Не претендует на layout (это делает детектор) —
    проверяет КОНТРАКТ HyperFrames + детерминизм.
    Возвращает (ok, issues)."""
    issues: list[str] = []
    p = Path(path)
    if not p.exists():
        return False, ["файл не существует"]
    try:
        html = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, [f"не прочитать: {e}"]

    if len(html) < 5000:
        issues.append(f"слишком короткий ({len(html)} байт < 5000) — вероятно каркас")
    if "data-composition-id" not in html:
        issues.append("нет data-composition-id")
    if "data-width" not in html or "data-height" not in html:
        issues.append("нет data-width/data-height")
    if "window.__timelines" not in html:
        issues.append("нет window.__timelines[...] регистрации")
    if "gsap.timeline" not in html:
        issues.append("нет gsap.timeline")
    # детерминизм (рендер по кадрам сломается на random/clock/бесконечном repeat)
    if "Math.random" in html:
        issues.append("Math.random — недетерминизм (рендер по кадрам сломается)")
    if "Date.now" in html or "new Date(" in html:
        issues.append("Date.now/new Date — недетерминизм")
    if "repeat:-1" in html.replace(" ", "") or "repeat: -1" in html:
        issues.append("repeat:-1 — бесконечный цикл")
    # Внешние http(s) URL — рендер обычно оффлайн, ПРОИЗВОЛЬНЫЕ ассеты ломают
    # детерминизм. Но есть LIBRARY-CDN'ы (GSAP, шрифты), за которыми сам
    # HyperFrames-рендер ходит при инициализации сцены — их разрешаем.
    # Whitelist строгий: точное совпадение хоста (чтобы typosquat вроде
    # cdn.jsdelivr.net.attacker.com не пролез). w3.org — SVG namespace.
    _URL_WHITELIST = {
        "www.w3.org",            # xmlns SVG
        "cdn.jsdelivr.net",      # GSAP и др. библиотеки (стандарт для HF)
        "fonts.googleapis.com",  # Google Fonts CSS
        "fonts.gstatic.com",     # Google Fonts woff2
        "unpkg.com",             # npm CDN
    }
    import re as _re
    bad_hosts: list[str] = []
    for m in _re.finditer(r"https?://([a-zA-Z0-9.-]+)", html):
        host = m.group(1).lower()
        if host not in _URL_WHITELIST:
            bad_hosts.append(host)
    if bad_hosts:
        sample = ", ".join(sorted(set(bad_hosts))[:3])
        issues.append(
            f"внешний http(s) URL вне whitelist ({sample}) — рендер оффлайн, "
            f"произвольные ассеты не подгружаются; разрешены только: "
            f"{', '.join(sorted(_URL_WHITELIST))}"
        )
    return (len(issues) == 0), issues


def _clear_scene_files() -> None:
    """Удаляет старые scene_NN.html перед посценной генерацией. Иначе
    `_scene_done` даст ЛОЖНЫЙ успех на устаревшем файле, если Claude не
    перепишет сцену (например при retry из бота)."""
    for sf in SCENE_FILES:
        p = HF_PROJECT / sf
        if p.exists():
            try:
                p.unlink()
            except Exception as e:
                logger.warning(f"[hf_broll] не удалил старый {sf}: {e}")


def _scene_contract(storyboard: dict, scene_id: str) -> dict:
    for s in (storyboard.get("scenes") or []):
        if s.get("id") == scene_id:
            return s
    return {}


def _build_scene_prompt(storyboard: dict, scene_id: str, done_scenes: list[dict]) -> str:
    """Промпт на ОДНУ сцену. Короткий → укладывается в SCENE_BUILD_TIMEOUT.

    Передаёт контракт сцены из storyboard + **единый style contract** (Phase 1
    Step 2, 5 июня) который inline-ится одинаковым для всех 6 параллельных
    subagent'ов. Это заменяет cross-talk done_scenes (который не работает при
    параллели) на статичную дизайн-систему.

    done_scenes — оставлен для обратной совместимости с последовательным циклом,
    но при параллельном scheduler'е будет всегда [].
    """
    # Загружаем style contract один раз на вызов (~1KB, дешево). При запуске
    # ОЧЕНЬ десятков параллельных вызовов можно кэшировать на module-level,
    # но сейчас 6 сцен × 1KB = шум.
    from style_contract import load_style_contract, inline_for_prompt
    contract = load_style_contract()
    style_block = inline_for_prompt(contract)

    sc = _scene_contract(storyboard, scene_id)
    done_block = ""
    if done_scenes:
        rows = "\n".join(
            f"  {d.get('id')}: {d.get('archetype')} — «{d.get('primary_text')}»"
            for d in done_scenes
        )
        done_block = (
            "УЖЕ ГОТОВЫЕ СЦЕНЫ (для единства стиля — держи ту же дизайн-систему, "
            "но НЕ переписывай их файлы и НЕ повторяй их визуальный приём):\n"
            f"{rows}\n\n"
        )
    return f"""Ты — моушн-дизайнер студии HyperFrames. АВТОНОМНЫЙ режим: не задавай
вопросов, НЕ используй AskUserQuestion — сделай задачу до конца сам.

Создай РОВНО ОДНУ композицию `{scene_id}.html` в корне проекта по контракту из
утверждённой раскадровки. НЕ трогай другие scene-файлы, index.html, design.md.

КОНТРАКТ ЭТОЙ СЦЕНЫ ({scene_id}):
{json.dumps(sc, ensure_ascii=False, indent=1)}

{done_block}ПРОЧИТАЙ РОВНО ДВА файла (один раз, в начале — больше ничего читать НЕ нужно):
- `reference_pack.md` — выжимка правил HyperFrames: визуальный вокабуляр,
  motion-правила, анти-паттерны, safe-area, data-* атрибуты. Это ВСЁ, что нужно;
  НЕ читай файлы скилла и design.md — их содержимое уже сведено в reference_pack.
- `index.html` — образец композиции + блок `@font-face` (скопируй его дословно).

ГЛАВНОЕ ПРАВИЛО АРХЕТИПА:
Реализуй ИМЕННО business_archetype / hf_technique / visual_style /
motion_family из КОНТРАКТА СЦЕНЫ выше. primary_text — главный текст на экране.

{style_block}

🔴 ЖЁСТКО ПРО СКОРОСТЬ (нарушение = провал задачи):
- НЕ запускай НИКАКИХ команд: ни `npx hyperframes lint`, ни `validate`,
  ни `inspect`, ни `render`, ни `ls`. У тебя НЕТ доступа к Bash — не пытайся.
- Как только записал `{scene_id}.html` через Write — НЕМЕДЛЕННО заверши ответ.
  НЕ читай файл обратно, НЕ делай Edit-подгонки «на всякий случай».
- Цель: один продуманный Write и стоп. Рендер, lint и layout-проверку делает
  оркестратор ОТДЕЛЬНО после тебя — тебе это делать НЕ нужно и ВРЕДНО (съедает
  время). Качество закладывай СРАЗУ в Write, а не итерациями."""


# ── Phase 1 Step 6: production-grade pre-flight + async build + native render

def _probe_ratelimit_now() -> dict | None:
    """Дёшевый probe rate-limit состояния Max-подписки.
    Запускает тривиальный `claude -p "1+1"` с --max-turns 1, парсит stream,
    возвращает rate_limit_info dict (или None при ошибке).
    Время ~5-7 сек, токенов копейки.
    """
    env = dict(os.environ)
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop("ANTHROPIC_API_KEY", None)
    env.setdefault("HOME", str(HF_PROJECT.parent))
    try:
        proc = subprocess.run(
            ["claude", "-p", "1+1",
             "--output-format", "stream-json", "--verbose", "--max-turns", "1"],
            cwd=HF_PROJECT, env=env,
            capture_output=True, text=True, timeout=60,
        )
        diag = _parse_stream(proc.stdout or "")
        return diag.get("rate_limit")
    except Exception as e:
        logger.warning(f"[hf_broll] probe_ratelimit failed: {e}")
        return None


def _check_ratelimit_before_batch(default_concurrency: int = 2) -> int:
    """Pre-flight gate перед параллельным батчем. Возвращает effective concurrency
    или бросает HyperFramesBrollError если rate-limit делает прогон бессмысленным.

    Логика (по ревью ChatGPT 4 июня):
      status=rejected            → raise (повтор упрётся в стену)
      util >= 0.9                → raise (5 минут гнать и упрёшься)
      util >= 0.7                → concurrency=1 (не нагружаем окно)
      util >= 0.5                → concurrency=min(default, 2)
      иначе (allowed, util<0.5)  → default
    """
    info = _probe_ratelimit_now()
    if not isinstance(info, dict):
        logger.info("[hf_broll] probe_ratelimit вернул None — продолжаю с default concurrency")
        return default_concurrency
    if info.get("status") == "rejected":
        note = _rate_limit_note(info) or "rate-limit rejected"
        raise HyperFramesBrollError(
            f"Pre-flight rate-limit: {note}. Запусти позже."
        )
    util = info.get("utilization")
    if isinstance(util, (int, float)):
        if util >= 0.9:
            raise HyperFramesBrollError(
                f"Pre-flight rate-limit: utilization={util:.0%} — окно почти исчерпано, "
                f"батч бессмыслен. Запусти позже."
            )
        if util >= 0.7:
            logger.warning(f"[hf_broll] rate-limit util={util:.0%} → concurrency понижен до 1")
            return 1
        if util >= 0.5:
            return min(default_concurrency, 2)
    return default_concurrency


def _build_scene_prompt_for_attempt(storyboard: dict, scene_id: str,
                                    attempt_dir: Path) -> str:
    """Промпт сцены с абсолютным путём в workspace-sandbox.

    По Step 1 (workspace isolation) каждая попытка пишется в свой attempt_dir,
    потом валидный HTML promoted в HF_PROJECT. Промпт говорит Claude писать
    через Write абсолютный путь — agent не конкурирует с другими сценами за
    общую папку.
    """
    base = _build_scene_prompt(storyboard, scene_id, [])
    abs_target = attempt_dir / f"{scene_id}.html"
    # перезаписываем относительный путь scene_NN.html на абсолютный
    sandbox_note = (
        f"\n\n🔵 РАБОЧАЯ ПАПКА (sandbox этой попытки):\n"
        f"  {attempt_dir}\n"
        f"  Пиши результат Write по АБСОЛЮТНОМУ пути: {abs_target}\n"
        f"  reference_pack.md и index.html лежат в {HF_PROJECT} — читай их "
        f"оттуда по абсолютному пути.\n"
    )
    return base + sandbox_note


# ── Single-shot build (10 июня) ──────────────────────────────────────
# Агентный headless-build (`claude -p` + Read/Write tools) зависал на сервере
# 18/18: Write вне workspace → permission-тупик в -p (некому подтвердить).
# Прод-паттерн для одно-файловых выходов (Remotion AI docs, screenshot-to-code,
# v0): ОДНА completion без инструментов, файл пишет вызывающий код. Вход тот же,
# что у доказанных десктоп-субагентов (reference_pack + index.html), только
# инлайном в промпт. Транспорт — SubscriptionClient (проверенный путь бота,
# с авто-ретраем на транзиентные сбои).

# Opus 4.8 — решение Артёма 10 июня, подтверждено A/B на scene_01:
# Opus 46с / 9.6KB / 1 layout-замечание vs Sonnet 74с / 15.5KB / 3.
# Кадр Opus богаче (оси, легенда, аннотации) при чистоте. Скорость НЕ хуже.
# Цена на подписке = flat, но Opus ест отдельное недельное окно seven_day_opus
# (шарится со скриптами бота) — при упоре в лимит переключить env на sonnet.
HF_SINGLESHOT_MODEL = os.getenv("HF_SINGLESHOT_MODEL", "claude-opus-4-8")
SINGLESHOT_MAX_TOKENS = 16000  # сцена ~13KB ≈ 4-5k токенов, запас ×3


def _load_inline_refs() -> tuple[str, str]:
    """(reference_pack, index_sample) из HF_PROJECT — инлайн-контекст промпта."""
    ref_p = HF_PROJECT / REFERENCE_PACK_FILE
    idx_p = HF_PROJECT / "index.html"
    if not ref_p.exists():
        raise HyperFramesBrollError(f"Нет {ref_p} — single-shot промпт неполон")
    if not idx_p.exists():
        raise HyperFramesBrollError(f"Нет {idx_p} — нет образца @font-face")
    return (ref_p.read_text(encoding="utf-8", errors="replace"),
            idx_p.read_text(encoding="utf-8", errors="replace"))


def _extract_html(text: str) -> str:
    """Ответ модели → чистый HTML-документ.

    Снимает ```-фенсы и прозу до/после. Требует и <html, и </html> —
    иначе ValueError (обрезанный ответ → пусть сработает retry)."""
    t = (text or "").strip()
    if "```" in t:
        # содержимое первого фенс-блока (```html\n...\n```)
        parts = t.split("```")
        if len(parts) >= 3:
            inner = parts[1]
            if inner.startswith(("html\n", "html\r\n")):
                inner = inner.split("\n", 1)[1]
            t = inner.strip()
    low = t.lower()
    start = low.find("<!doctype")
    if start < 0:
        start = low.find("<html")
    end = low.rfind("</html>")
    if start < 0 or end < 0 or end <= start:
        raise ValueError(
            f"ответ не содержит полного HTML-документа "
            f"(start={start}, end={end}, len={len(t)})"
        )
    return t[start:end + len("</html>")]


def _build_scene_singleshot_prompt(storyboard: dict, scene_id: str,
                                   prev_html: str | None = None,
                                   issues: str | None = None) -> str:
    """Самодостаточный промпт одной сцены: ВСЁ инлайном, никаких инструментов.

    issues → режим regenerate: прошлый HTML + дефекты, вернуть исправленный
    ПОЛНЫЙ документ (не патч)."""
    from style_contract import load_style_contract, inline_for_prompt
    style_block = inline_for_prompt(load_style_contract())
    ref_pack, idx_sample = _load_inline_refs()
    sc = _scene_contract(storyboard, scene_id)

    fix_block = ""
    if issues:
        fix_block = f"""
🔧 ЭТО ПОВТОРНАЯ ГЕНЕРАЦИЯ. Прошлая версия сцены имела ДЕФЕКТЫ:
{issues}

ПРОШЛАЯ ВЕРСИЯ (исправь её проблемы, сохрани сильные стороны):
{prev_html or "(не сохранилась)"}

Верни ИСПРАВЛЕННЫЙ ПОЛНЫЙ документ целиком.
"""

    return f"""Ты — моушн-дизайнер студии HyperFrames. Создай РОВНО ОДНУ \
композицию `{scene_id}` (вертикаль 1080×1920, 5 секунд, 30fps) по контракту \
из утверждённой раскадровки.

КОНТРАКТ ЭТОЙ СЦЕНЫ ({scene_id}):
{json.dumps(sc, ensure_ascii=False, indent=1)}

ГЛАВНОЕ ПРАВИЛО АРХЕТИПА:
Реализуй ИМЕННО business_archetype / hf_technique / visual_style /
motion_family из контракта выше. primary_text — главный текст на экране.

{style_block}

══════ ПРАВИЛА HYPERFRAMES (выжимка скилла — это ВСЁ, что нужно) ══════
{ref_pack}

══════ ОБРАЗЕЦ КОМПОЗИЦИИ (структура + @font-face, скопируй шрифты дословно) ══════
{idx_sample}
══════ КОНЕЦ ОБРАЗЦА ══════
{fix_block}
📤 ФОРМАТ ОТВЕТА (строго):
- Выведи ТОЛЬКО полный HTML-документ: первый символ ответа — `<!doctype html>`,
  последний — `</html>`.
- БЕЗ markdown-фенсов, БЕЗ пояснений до или после, БЕЗ комментариев «вот сцена».
- Документ самодостаточен: инлайн CSS/JS, gsap.timeline({{paused:true}}),
  регистрация window.__timelines["{scene_id}"], data-composition-id="{scene_id}",
  data-width="1080" data-height="1920". Никаких Math.random/Date.now/repeat:-1,
  никаких внешних URL кроме разрешённых CDN."""


def _singleshot_llm_client():
    """LLM-клиент: подписка (SubscriptionClient, как у бота) или API-fallback.

    Таймаут свой (НЕ ботовский 180с): полный HTML сцены ~13KB генерится
    дольше (smoke 10 июня: 2×180с не хватило)."""
    tok = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    timeout_s = int(os.getenv("HF_SINGLESHOT_TIMEOUT_S", "480"))
    if tok:
        from claude_subscription import SubscriptionClient
        # MAX_THINKING_TOKENS=0: смок 10 июня — с дефолтным thinking сцена
        # НЕ влезала в 2×480с, без него та же сцена = 74с (~110 ток/с).
        return SubscriptionClient(
            tok, timeout_sec=timeout_s,
            extra_env={"MAX_THINKING_TOKENS": "0"},
        )
    import anthropic
    return anthropic.Anthropic(api_key=_anthropic_key())


def _singleshot_generate_scene(storyboard: dict, scene_id: str,
                               prev_html: str | None = None,
                               issues: str | None = None) -> str:
    """Один вызов LLM → готовый HTML сцены (или ValueError/RuntimeError)."""
    prompt = _build_scene_singleshot_prompt(
        storyboard, scene_id, prev_html=prev_html, issues=issues)
    client = _singleshot_llm_client()
    resp = client.messages.create(
        model=HF_SINGLESHOT_MODEL,
        max_tokens=SINGLESHOT_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_html(resp.content[0].text)


async def _run_build_phase_singleshot(storyboard: dict, job) -> float:
    """Параллельный single-shot build: semaphore-bounded, regenerate на дефектах.

    На каждую сцену до MAX_SCENE_BUILD_ATTEMPTS попыток; невалидный ответ
    (обрыв/каркас/недетерминизм) идёт в СЛЕДУЮЩУЮ попытку как «прошлая версия
    + дефекты» — модель чинит, а не гадает заново. Валидный → promote в
    HF_PROJECT (атомарно через JobContext)."""
    import asyncio

    concurrency = _check_ratelimit_before_batch(default_concurrency=int(
        os.getenv("HF_BUILD_CONCURRENCY", "2")))
    sem = asyncio.Semaphore(concurrency)
    all_scene_ids = [f"scene_{i:02d}" for i in range(1, N_INSERTS + 1)]

    async def build_one(sid: str) -> bool:
        prev_html: str | None = None
        prev_issues: str | None = None
        for attempt_n in range(1, MAX_SCENE_BUILD_ATTEMPTS + 1):
            t0 = time.time()
            async with sem:
                try:
                    html = await asyncio.to_thread(
                        _singleshot_generate_scene, storyboard, sid,
                        prev_html, prev_issues)
                except (ValueError, RuntimeError) as e:
                    logger.warning(
                        f"[hf_broll] {sid} attempt {attempt_n}: LLM-вызов: {e}")
                    prev_issues = f"прошлый ответ не разобрался: {e}"
                    prev_html = None
                    continue
            adir = job.attempt_dir(sid, attempt_n)
            target = adir / f"{sid}.html"
            target.write_text(html, encoding="utf-8")
            ok, iss = _scene_valid_minimal(target, sid)
            dur = time.time() - t0
            if ok:
                job.promote(sid, target, HF_PROJECT)
                job.finalize_scene(sid, "ok", attempt_n=attempt_n,
                                   duration_s=dur)
                logger.info(
                    f"[hf_broll] {sid} ok @ attempt {attempt_n} "
                    f"(single-shot, {dur:.0f}s, {len(html)} chars)")
                return True
            logger.warning(
                f"[hf_broll] {sid} attempt {attempt_n}: невалиден ({iss})")
            prev_html, prev_issues = html, "; ".join(iss)
        job.finalize_scene(sid, "failed",
                           reason=f"{MAX_SCENE_BUILD_ATTEMPTS} попыток исчерпаны")
        return False

    results = await asyncio.gather(*(build_one(s) for s in all_scene_ids))
    failed = [sid for sid, ok in zip(all_scene_ids, results) if not ok]
    if failed:
        raise HyperFramesBrollError(
            f"{len(failed)} сцен не сгенерированы за {MAX_SCENE_BUILD_ATTEMPTS} "
            f"попыток (single-shot): {', '.join(failed)}"
        )
    return 0.0  # подписка flat-fee; метеред-стоимость не агрегируем здесь


def _problems_by_scene(layout_by_scene: dict, render_errors: list[str]) -> dict[str, str]:
    """{scene_file: текст дефектов} для per-scene fix-round.

    layout_by_scene — {scene_file: [issues]} из _inspect_all_scenes;
    render_errors — строки формата 'scene_NN.html: ...' из _render_all*."""
    out: dict[str, list[str]] = {}
    for sf, issues in (layout_by_scene or {}).items():
        out.setdefault(sf, []).append(_format_layout_issues({sf: issues}))
    for err in (render_errors or []):
        sf = err.split(":", 1)[0].strip()
        if sf in SCENE_FILES:
            out.setdefault(sf, []).append(f"ОШИБКА РЕНДЕРА: {err}")
        else:
            logger.warning(f"[hf_broll] render-ошибка без сцены-префикса: {err[:120]}")
    return {sf: "\n\n".join(parts) for sf, parts in out.items()}


def _fix_scenes_singleshot(storyboard: dict, problems: dict[str, str]) -> None:
    """Per-scene regenerate ТОЛЬКО проблемных сцен (fix-round single-shot).

    Новый HTML пишется в HF_PROJECT только если прошёл _scene_valid_minimal —
    иначе оставляем старый (рендерибельный хуже, чем невалидный)."""
    for sf, issue_text in problems.items():
        sid = sf.replace(".html", "")
        path = HF_PROJECT / sf
        prev = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
        try:
            html = _singleshot_generate_scene(storyboard, sid,
                                              prev_html=prev, issues=issue_text)
        except (ValueError, RuntimeError) as e:
            logger.warning(f"[hf_broll] fix {sid}: LLM-вызов не удался: {e}")
            continue
        import tempfile as _tf
        with _tf.NamedTemporaryFile("w", encoding="utf-8", suffix=".html",
                                    delete=False) as f:
            f.write(html)
            tmp = Path(f.name)
        ok, iss = _scene_valid_minimal(tmp, sid)
        if ok:
            path.write_text(html, encoding="utf-8")
            logger.info(f"[hf_broll] fix {sid}: перегенерирован ({len(html)} chars)")
        else:
            logger.warning(f"[hf_broll] fix {sid}: новый HTML невалиден ({iss}) — оставил старый")
        tmp.unlink(missing_ok=True)


async def _run_build_phase_async(storyboard: dict, job) -> float:
    """Async параллельный build через SceneScheduler (Phase 1 Step 6).

    Заменяет последовательный _run_build_phase. Шаги:
      1. Pre-flight rate-limit → effective concurrency.
      2. SceneScheduler(concurrency) — Semaphore-bounded async-batch.
      3. Для каждой сцены до MAX_SCENE_BUILD_ATTEMPTS попыток.
      4. Валидные HTML promoted из attempt_dir → HF_PROJECT (atomic).
      5. job.finalize_scene для каждой завершённой.
      6. raise HyperFramesBrollError если хоть одна не сдалась.
    """
    from scene_scheduler import SceneScheduler

    concurrency = _check_ratelimit_before_batch(default_concurrency=int(
        os.getenv("HF_BUILD_CONCURRENCY", "2")))
    scheduler = SceneScheduler(
        concurrency=concurrency,
        idle_timeout=float(os.getenv("HF_IDLE_TIMEOUT_S", "60")),
        wall_timeout=float(os.getenv("HF_WALL_TIMEOUT_S", str(SCENE_BUILD_TIMEOUT))),
    )

    all_scene_ids = [f"scene_{i:02d}" for i in range(1, N_INSERTS + 1)]
    done: dict[str, SceneResult] = {}  # type: ignore

    for attempt_n in range(1, MAX_SCENE_BUILD_ATTEMPTS + 1):
        remaining = [sid for sid in all_scene_ids if sid not in done]
        if not remaining:
            break
        logger.info(
            f"[hf_broll] build-phase attempt {attempt_n}/{MAX_SCENE_BUILD_ATTEMPTS}: "
            f"{len(remaining)} сцен"
        )
        prompts = {
            sid: _build_scene_prompt_for_attempt(
                storyboard, sid, job.attempt_dir(sid, attempt_n)
            )
            for sid in remaining
        }
        results = await scheduler.build_all(prompts, job, attempt_n=attempt_n)

        # promote успешно записанные + валидные
        for sid, r in results.items():
            html = job.attempt_dir(sid, attempt_n) / f"{sid}.html"
            if html.exists():
                ok, iss = _scene_valid_minimal(html, sid)
                if ok:
                    job.promote(sid, html, HF_PROJECT)
                    done[sid] = r
                    job.finalize_scene(
                        sid, "ok", attempt_n=attempt_n,
                        turns=r.turns, cost_usd=r.cost_usd,
                        duration_s=r.duration_s,
                    )
                    logger.info(
                        f"[hf_broll] {sid} ok @ attempt {attempt_n} "
                        f"(turns={r.turns}, ${r.cost_usd:.2f}, {r.duration_s:.0f}s)"
                    )
                else:
                    logger.warning(
                        f"[hf_broll] {sid} attempt {attempt_n}: HTML невалиден ({iss})"
                    )
            else:
                logger.warning(
                    f"[hf_broll] {sid} attempt {attempt_n}: HTML не записан "
                    f"(scheduler status={r.status}, reason={r.reason!r})"
                )

    # финал — все сдались?
    failed = [sid for sid in all_scene_ids if sid not in done]
    if failed:
        # фиксируем причину для дебага
        for sid in failed:
            job.finalize_scene(sid, "failed", reason=f"{MAX_SCENE_BUILD_ATTEMPTS} попыток исчерпаны")
        raise HyperFramesBrollError(
            f"{len(failed)} сцен не сгенерированы за {MAX_SCENE_BUILD_ATTEMPTS} попыток: "
            f"{', '.join(failed)}"
        )
    return sum(r.cost_usd for r in done.values())


def _run_motion_gate() -> None:
    """Прогон motion smoke-test на всех 6 scene_NN.html в HF_PROJECT.

    Вызывается ПОСЛЕ build, ДО render — ловит scene_04-style баги
    (timeline зарегистрирован, но визуально статичен) до того как мы
    потратили время на ffmpeg.

    Raise HyperFramesBrollError если хотя бы одна сцена даёт is_blocking
    verdict (fail / no_timeline / error). warning не блокирует.
    """
    from motion_smoketest import check_motion

    blocked: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    for sf in SCENE_FILES:
        p = HF_PROJECT / sf
        if not p.exists():
            blocked.append((sf, "html отсутствует"))
            continue
        v = check_motion(p)
        if v.is_blocking:
            blocked.append((sf, f"{v.verdict}: {v.reason or 'motion ~ 0'}"))
        elif v.verdict == "warning":
            warnings.append((sf, f"motion {v.max_diff_pct:.2%}"))

    if warnings:
        logger.info(
            f"[hf_broll] motion gate: {len(warnings)} warnings (не блокируют): "
            + "; ".join(f"{n}:{r}" for n, r in warnings)
        )
    if blocked:
        raise HyperFramesBrollError(
            f"Motion gate FAIL: {len(blocked)} сцен. "
            + "; ".join(f"{n} ({r})" for n, r in blocked)
        )


def _render_all_native(out_dir: Path) -> tuple[list[Path], list[str]]:
    """Render через наш tools/render_scene.mjs + ffmpeg (Phase 1 Step 6).

    Заменяет _render_all (npx hyperframes). По Step 4 (parity-test) у npx
    67% совместимость с subagent-generated сценами (scene_01 → чёрный экран).
    Наш puppeteer-based render даёт 100%.

    Pipeline на сцену:
      1. node tools/render_scene.mjs scene.html frames_dir 5 30 → 150 PNG
      2. ffmpeg -framerate 30 -i frame_%04d.png ... → hf_NN.mp4

    Возвращает (готовые_клипы, ошибки) — тот же контракт что _render_all.
    """
    render_script = Path(__file__).resolve().parent / "tools" / "render_scene.mjs"
    if not render_script.exists():
        return [], [f"render_scene.mjs не найден: {render_script}"]

    hf_dir = out_dir / "hyperframes"
    hf_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    errors: list[str] = []

    for i, scene_file in enumerate(SCENE_FILES, start=1):
        scene_path = HF_PROJECT / scene_file
        if not scene_path.exists():
            errors.append(f"{scene_file}: файл не создан Клодом")
            continue

        frames_dir = hf_dir / f"_frames_{i:02d}"
        frames_dir.mkdir(parents=True, exist_ok=True)
        out_mp4 = hf_dir / f"hf_{i:02d}.mp4"

        # 1) puppeteer → 150 PNG
        try:
            r1 = subprocess.run(
                ["node", str(render_script), str(scene_path),
                 str(frames_dir), "5", "30"],
                cwd=HF_PROJECT, env=_render_env(),
                capture_output=True, text=True, timeout=RENDER_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{scene_file}: render_scene.mjs таймаут")
            continue
        if r1.returncode != 0:
            tail = (r1.stderr or r1.stdout)[-500:]
            errors.append(f"{scene_file}: render_scene.mjs rc={r1.returncode}: {tail}")
            continue

        # 2) ffmpeg склейка
        try:
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-framerate", "30",
                 "-i", str(frames_dir / "frame_%04d.png"),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-crf", "20", "-preset", "medium",
                 str(out_mp4)],
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{scene_file}: ffmpeg таймаут")
            continue
        if r2.returncode != 0 or not out_mp4.exists():
            tail = (r2.stderr or "")[-500:]
            errors.append(f"{scene_file}: ffmpeg rc={r2.returncode}: {tail}")
            continue
        clips.append(out_mp4)

    return clips, errors


# import для типов в _run_build_phase_async (ленивый, чтобы не падать на старте)
try:
    from scene_scheduler import SceneResult  # noqa: F401
except Exception:
    pass


def _run_build_phase(storyboard: dict) -> float:
    """Фаза 2: пишет 6 сцен ПОСЦЕННО (каждая — отдельный claude -p со своим
    таймаутом). Готовые сцены передаются в следующие промпты (единство стиля).
    Бросает HyperFramesBrollError, если сцена не записалась за
    MAX_SCENE_BUILD_ATTEMPTS попыток."""
    cost = 0.0
    done: list[dict] = []
    _clear_scene_files()  # иначе _scene_done даст ложный успех на старых файлах
    for i, scene_file in enumerate(SCENE_FILES, start=1):
        scene_id = f"scene_{i:02d}"
        sc = _scene_contract(storyboard, scene_id)
        written = False
        for attempt in range(1, MAX_SCENE_BUILD_ATTEMPTS + 1):
            try:
                cost += _run_claude(
                    _build_scene_prompt(storyboard, scene_id, done),
                    timeout=SCENE_BUILD_TIMEOUT,
                    # Bash УБРАН: чтобы Claude не запускал mandatory
                    # `npx hyperframes lint/validate/inspect` из скилла (cold npx
                    # = минуты; диагностика 3 июня поймала Bash×3 после Write).
                    tools="Read,Edit,Write,Glob,Grep",
                    # turn-cap: отличает «медленный API» от «агент ходит кругами»
                    # (self-loop правок). На сцену хватает ~6 ходов (Read+Write).
                    max_turns=8,
                )
            except HyperFramesTimeout:
                # Claude часто ЗАПИСЫВАЕТ scene_NN.html, но не завершает сессию
                # в срок (3 июня: пишет HTML за ~7 мин, потом «думает» → timeout
                # 10 мин, хотя файл уже на диске и валиден). Сначала проверяем
                # файл — если записан И валиден, ПРИНИМАЕМ (не выбрасываем работу).
                _revert_stray()
                ok, iss = _scene_valid_minimal(HF_PROJECT / scene_file, scene_id)
                if ok:
                    logger.info(
                        f"[hf_broll] {scene_id} записан до таймаута — принимаю "
                        f"(Claude не завершился в срок, но HTML валиден)"
                    )
                    written = True
                    break
                # Файла нет / каркас → таймаут реально потерял работу. retry:
                # скорость API гуляет, повтор может попасть в быстрый период
                # (Max-подписка, retry «бесплатный»).
                logger.warning(
                    f"[hf_broll] {scene_id} таймаут, валидного файла нет (попытка "
                    f"{attempt}/{MAX_SCENE_BUILD_ATTEMPTS}) — retry. issues={iss}"
                )
                continue
            _revert_stray()  # откатить постороннее, scene+storyboard сохранить
            ok, iss = _scene_valid_minimal(HF_PROJECT / scene_file, scene_id)
            if ok:
                written = True
                break
            logger.warning(
                f"[hf_broll] {scene_id} невалиден (попытка {attempt}/"
                f"{MAX_SCENE_BUILD_ATTEMPTS}): issues={iss}"
            )
        if not written:
            raise HyperFramesBrollError(
                f"{scene_id} не сгенерирован за {MAX_SCENE_BUILD_ATTEMPTS} попыток "
                f"(таймаут/пусто)"
            )
        done.append({
            "id": scene_id,
            "archetype": sc.get("business_archetype", "?"),
            "primary_text": sc.get("primary_text", ""),
        })
        logger.info(f"[hf_broll] {scene_id} готов ({i}/{N_INSERTS})")
    return cost


def _build_prompt(script_text: str, fix_error: str | None = None,
                  storyboard: dict | None = None) -> str:
    if fix_error:
        return (
            "Одна или несколько композиций scene_NN.html, которые ты записал, "
            "НЕ проходят рендер HyperFrames. Ошибки:\n\n"
            f"{fix_error}\n\n"
            "Исправь только проблемные scene_NN.html так, чтобы "
            "`npx hyperframes render -c scene_NN.html` проходил без ошибок. "
            "Используй skill /hyperframes (.agents/skills/hyperframes/SKILL.md). "
            "Не трогай index.html, design.md, fonts/. Сохрани 6 файлов "
            "scene_01.html…scene_06.html."
        )
    # Build-фаза по утверждённому storyboard (если есть): каждая сцена
    # реализует СВОЙ архетип/технику/стиль из раскадровки. Это убивает
    # монотонность по построению (6 разных архетипов уже зафиксированы).
    storyboard_block = ""
    if storyboard:
        storyboard_block = (
            "\nУТВЕРЖДЁННАЯ РАСКАДРОВКА (storyboard.json — реализуй КАЖДУЮ сцену "
            "строго по её контракту: business_archetype, hf_technique, "
            "visual_style, motion_family, density, scale_profile, primary_text):\n"
            + json.dumps(storyboard, ensure_ascii=False, indent=1)
            + "\n\nКаждый scene_NN.html — это реализация одноимённой сцены из "
            "раскадровки. НЕ меняй архетипы и не делай сцены одинаковыми — "
            "раскадровка уже прошла валидацию на разнообразие.\n"
            "Дополнительно прочитай `reference_pack.md` (визуальный вокабуляр, "
            "анти-паттерны, motion-правила).\n"
        )

    return f"""Ты — моушн-дизайнер студии. Создай 6 коротких графических
B-roll-вставок под сценарий ролика для Telegram-канала предпринимателя
(картинг + глэмпинг Life Drive, Тюмень), используя HyperFrames.
Это АВТОНОМНЫЙ режим: не задавай вопросов, не используй AskUserQuestion.

СЦЕНАРИЙ (озвучка аватара, ~30 секунд):
─────────────────────────────────────
{script_text}
─────────────────────────────────────
{storyboard_block}
ОБЯЗАТЕЛЬНО ПЕРЕД РАБОТОЙ:
1. Прочитай skill: `.agents/skills/hyperframes/SKILL.md` — это правила
   HyperFrames (data-* атрибуты, window.__timelines, clip-visibility,
   запреты). Следуй им буквально.
2. Прочитай `design.md` — фирменная дизайн-система (цвета, шрифты, motion).
   Используй её точные значения, не выдумывай цвета.
3. Посмотри `index.html` как рабочий образец: @font-face на шрифты из
   `fonts/` (Inter Tight 800 / Inter 500/600, cyrillic+latin), структура
   standalone-композиции, регистрация таймлайна.

ЧТО СДЕЛАТЬ:
- Раздели сценарий на 6 визуальных моментов в хронологическом порядке.
- Создай 6 ОТДЕЛЬНЫХ STANDALONE-композиций в КОРНЕ проекта:
  `scene_01.html` … `scene_06.html`. scene_01 — первый момент, scene_06 —
  последний.
- Каждый файл — самостоятельный (как index.html), БЕЗ <template>-обёртки
  (его рендерят напрямую через `hyperframes render -c scene_NN.html`).

ПРАВИЛА (нарушать нельзя):
- Размер кадра 1080×1920 (вертикаль). data-width="1080" data-height="1920".
- Длительность каждой вставки 5 секунд (data-duration="5").
- @font-face КОПИРУЙ из index.html — те же 6 woff2 из `fonts/` с unicode-range
  (cyrillic + latin). Текст РУССКИЙ — без кириллических шрифтов он не отрисуется.
- 🔴 SAFE-AREA: ВСЕ значимые элементы (текст, числа, карточки, графики) —
  ТОЛЬКО в центральной полосе 1080×960 (y∈[480,1440] по вертикали,
  x∈[40,1040] по горизонтали — оставь по 40px полей по бокам). Это
  безопасная зона, гарантированно видимая зрителю. Причина: в split-layout
  (когда B-roll делит экран с аватаром) бот обрезает кадр до 1080×960;
  всё что выше y=480 или ниже y=1440 — ИСЧЕЗНЕТ. Аналогично по X:
  карточки шире 1040 ОБРЕЖУТСЯ по правому краю (Артём 1 июня: «20%»
  карточка вылезла за экран). Вне safe-area допустимы ТОЛЬКО фоновые
  градиенты / декоративная подложка — никакого смыслового контента.
- 🔴 LAYOUT — ОБЯЗАТЕЛЬНО flex-column (по SKILL.md строка 73):
  • Контейнер контента — div размером 1000×960 (или меньше) с
    `display: flex; flex-direction: column; justify-content: center;
     gap: 48px; padding: 40px; box-sizing: border-box;`
    позиционируй его в центре: `position:absolute; left:50%; top:50%;
    transform: translate(-50%,-50%);`
  • Содержимое (заголовок, цифры, карточки, подписи) — БЕЗ собственного
    `position:absolute; top:Npx`. CSS gap сам даёт нужный воздух между
    блоками; padding контейнера — поля от края safe-area.
  • НЕ используй `position:absolute; top:Npx` на текстовых/контентных
    блоках. Это запрещено SKILL.md и приводит к наложениям (Claude
    считает высоту шрифта вслепую → пересечения).
  • Декоративные элементы (фон-градиент, ambient glow) — `position:
    absolute` допустим, они НЕ контент.
- 🔴 АНТИ-OVERLAP (следствие flex-column): bounding-box'ы видимых одновременно
  элементов не должны пересекаться. CSS gap≥40px между блоками гарантирует
  это автоматически. Если элементов много — уменьшай их или показывай
  последовательно во времени через таймлайн, не одновременно.
- Стиль строго по design.md: фон тёмный, accent оранжевый #FF5722.
- Только графика и моушн-дизайн: счётчики, графики, диаграммы, карточки,
  чек-листы, крупные цифры/проценты. НЕ изображать людей, лица, руки.
- Каждая вставка иллюстрирует КОНКРЕТНЫЙ момент сценария.
- Никаких выдуманных точных денежных цифр о бизнесе (рубли выручки/прибыли).
  Иллюстративные проценты и числа — допустимы.
- ЧИТАЕМОСТЬ ПРЕВЫШЕ ВСЕГО. Каждый одновременно видимый элемент — на своём
  data-track-index (одинаковые индексы на одной дорожке запрещены).
- Детерминизм: НЕ использовать Math.random(), Date.now(). repeat: -1 запрещён.

ОГРАНИЧЕНИЯ:
- Создавай/редактируй ТОЛЬКО scene_01.html…scene_06.html в корне.
- НЕ трогай index.html, design.md, fonts/, .agents/, package.json,
  hyperframes.json.

После записи 6 файлов — закончи. Рендер сделает оркестратор."""


# ── git-базлайн и откат лишних правок ────────────────────────────────
def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=HF_PROJECT,
        capture_output=True, text=True, timeout=60,
    )


def ensure_git_baseline() -> None:
    """Инициализирует git в проекте HyperFrames (один раз), чтобы можно
    было откатывать лишние правки Клода."""
    if (HF_PROJECT / ".git").exists():
        return
    _git(["init"])
    _git(["config", "user.email", "bot@maksim-bot"])
    _git(["config", "user.name", "maksim-bot"])
    # node_modules / chrome-headless-shell / renders — не версионируем
    gitignore = HF_PROJECT / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "node_modules/\nchrome-headless-shell/\nrenders/\n.cache/\n",
            encoding="utf-8",
        )
    _git(["add", "-A"])
    _git(["commit", "-m", "baseline"])
    logger.info("[hf_broll] git baseline создан")


def _revert_stray() -> None:
    """Откатывает все правки Клода КРОМЕ наших артефактов (scene_NN.html +
    storyboard.json). storyboard.json пишет фаза 1 — его НЕЛЬЗЯ откатывать."""
    if not (HF_PROJECT / ".git").exists():
        return
    allowed = set(SCENE_FILES) | {STORYBOARD_FILE}
    changed = _git(["diff", "--name-only"]).stdout.split()
    stray = [f for f in changed if f not in allowed]
    if stray:
        _git(["checkout", "--", *stray])
        logger.warning(f"[hf_broll] откатил лишние правки: {stray}")
    # снимок наших артефактов — текущая версия становится новым базлайном
    _git(["add", *SCENE_FILES, STORYBOARD_FILE])
    _git(["commit", "-m", "scenes update", "--allow-empty"])


# ── Claude Code ──────────────────────────────────────────────────────
def _parse_stream(stdout: str) -> dict:
    """Парсит stream-json (JSONL) → диагностика: tool_counts, num_events,
    последнее событие, финальный result. Используется и в success-ветке, и в
    timeout-ветке (закрыть слепое пятно: видеть, что Claude делал до таймаута).
    Мусорные/пустые строки игнорируем."""
    tool_counts: dict[str, int] = {}
    num_events = 0
    last_type = None
    result_event = None
    rate_limit = None  # последний rate_limit_info (троттлинг Max-подписки)
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except Exception:
            continue
        num_events += 1
        last_type = evt.get("type")
        if last_type == "result":
            result_event = evt
        elif last_type == "rate_limit_event":
            # CLI эмитит при ИЗМЕНЕНИИ rate-limit-инфо (claude 2.1.x). Берём
            # последний — он отражает актуальное состояние лимита.
            info = evt.get("rate_limit_info")
            if isinstance(info, dict):
                rate_limit = info
        elif last_type == "assistant":
            msg = evt.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        name = blk.get("name") or "?"
                        tool_counts[name] = tool_counts.get(name, 0) + 1
    return {
        "tool_counts": tool_counts,
        "num_events": num_events,
        "last_type": last_type,
        "result_event": result_event,
        "rate_limit": rate_limit,
    }


def _rate_limit_note(info) -> str | None:
    """Человекочитаемая заметка о троттлинге, или None если лимит НЕ исчерпан.

    info — `rate_limit_info` из rate_limit_event. Схема (claude 2.1.x):
      status: allowed|allowed_warning|rejected, resetsAt: epoch-сек,
      rateLimitType: five_hour|seven_day|seven_day_opus|seven_day_sonnet|overage,
      utilization: 0..1.
    Возвращаем текст ТОЛЬКО при status=rejected (реальная блокировка) — чтобы
    оркестратор не retry-ил вслепую в стену, а знал ЧТО за лимит и КОГДА сброс."""
    if not isinstance(info, dict):
        return None
    if info.get("status") != "rejected":
        return None  # allowed / allowed_warning — ещё можно работать
    parts = ["API-лимит исчерпан (Max-подписка)"]
    rl_type = info.get("rateLimitType")
    if rl_type:
        parts.append(f"тип: {rl_type}")
    resets = info.get("resetsAt")
    if isinstance(resets, (int, float)) and resets > 0:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(resets))
            parts.append(f"сброс ~{ts}")
        except Exception:
            parts.append(f"сброс epoch={int(resets)}")
    util = info.get("utilization")
    if isinstance(util, (int, float)):
        parts.append(f"utilization={util:.0%}")
    return "; ".join(parts)


def _run_claude(prompt: str, timeout: int | None = None,
                tools: str | None = None, max_turns: int | None = None) -> float:
    """Запускает Claude Code; возвращает total_cost_usd из json-ответа.

    timeout — сек на вызов (по умолчанию CLAUDE_TIMEOUT). Для per-scene build
    передаём меньший (SCENE_BUILD_TIMEOUT), чтобы зависшая сцена не съедала
    весь бюджет.
    tools — `--allowedTools` (по умолч. Read,Edit,Write,Glob,Grep). Для
    build-фазы передаём БЕЗ Bash, чтобы Claude не запускал mandatory
    `npx hyperframes lint/validate/inspect` из скилла (cold npx = минуты).
    max_turns — `--max-turns` (отличает «медленный API» от «агент ходит
    кругами»; turn-limit ≠ timeout)."""
    _timeout = timeout if timeout is not None else CLAUDE_TIMEOUT
    _tools = tools or "Read,Edit,Write,Glob,Grep"
    env = dict(os.environ)
    # Авторизация: подписка (CLAUDE_CODE_OAUTH_TOKEN) приоритетна — убираем
    # API-ключ из окружения, иначе CLI пойдёт по метеред-биллингу. Нет
    # токена → фолбэк на API-ключ. (Тот же паттерн, что в auto_broll.py.)
    oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if oauth_token:
        env.pop("ANTHROPIC_API_KEY", None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
        logger.info("[hf_broll] Claude Code auth: подписка (CLAUDE_CODE_OAUTH_TOKEN)")
    else:
        env["ANTHROPIC_API_KEY"] = _anthropic_key()
        logger.info("[hf_broll] Claude Code auth: API-ключ (метеред)")
    env.setdefault("HOME", str(Path(HF_PROJECT).parent))

    # Авто-retry на SIGTERM (_INTERRUPT_RC). 1 первая попытка + 1 retry.
    # При реальной ошибке Claude Code (rc=1 и пр.) — НЕ повторяем, она
    # повторится же. Кредиты не критерий (Max-подписка, см. reference).
    proc = None
    attempts_done = 0
    last_rc = None
    for attempt in range(1, _MAX_CLAUDE_ATTEMPTS + 1):
        attempts_done = attempt
        _cmd = [
            "claude", "-p", prompt,
            "--allowedTools", _tools,
            "--output-format", "stream-json", "--verbose",
        ]
        if max_turns is not None:
            _cmd += ["--max-turns", str(max_turns)]
        try:
            proc = subprocess.run(
                _cmd, cwd=HF_PROJECT, env=env,
                capture_output=True, text=True, timeout=_timeout,
            )
        except subprocess.TimeoutExpired as e:
            # Таймаут — НЕ повторяем. ВАЖНО: парсим partial stdout (он есть в
            # e.stdout) — закрываем слепое пятно: видим, ЧТО Claude делал до
            # таймаута (читал references? npx lint? долго думал перед Write?).
            mins = _timeout // 60
            diag = _parse_stream(getattr(e, "stdout", "") or "")
            tools_s = ", ".join(f"{k}={v}" for k, v in sorted(
                diag["tool_counts"].items(), key=lambda x: -x[1])) or "—"
            # Главный диагностический сигнал: троттлинг ли это? (3 июня scene_02:
            # last=rate_limit_event, Read=3, 0 Write → лимит, НЕ зависание агента).
            rl_note = _rate_limit_note(diag.get("rate_limit"))
            logger.error(
                f"[hf_broll] Claude Code не уложился в {mins} мин "
                f"(timeout={_timeout}s, попытка {attempt}). ДИАГНОСТИКА: "
                f"events={diag['num_events']}, last={diag['last_type']}, "
                f"tools=[{tools_s}]" + (f". ⛔ {rl_note}" if rl_note else "")
            )
            if rl_note:
                # Это НЕ зависание — это хард-лимит. Повторять бессмысленно
                # (вторая попытка упрётся в ту же стену). Сообщаем КОГДА сброс.
                raise HyperFramesTimeout(
                    f"Генерация прервана: {rl_note}. Повтор бессмысленен до "
                    f"сброса лимита — запусти позже."
                )
            raise HyperFramesTimeout(
                f"Claude Code не уложился за {mins} минут (timeout={_timeout}s). "
                f"Попробуй повторить или использовать Remotion-движок."
            )
        last_rc = proc.returncode
        if proc.returncode == 0:
            break  # успех
        if proc.returncode in _INTERRUPT_RC and attempt < _MAX_CLAUDE_ATTEMPTS:
            logger.warning(
                f"[hf_broll] Claude Code прерван (rc={proc.returncode}), "
                f"retry через {HF_RETRY_DELAY_SEC}s "
                f"(попытка {attempt}/{_MAX_CLAUDE_ATTEMPTS})…"
            )
            time.sleep(HF_RETRY_DELAY_SEC)
            continue
        # ничего больше не пробуем — выходим из цикла, ошибка ниже
        break

    if last_rc in _INTERRUPT_RC:
        # Все попытки получили SIGTERM. Это серьёзный сигнал
        # (постоянный OOM-killer / постоянный внешний kill / deploy
        # затянулся). Поднимаем Interrupted с пометкой про retry —
        # card_hfbroll покажет юзеру кнопку повтора.
        raise HyperFramesInterrupted(
            f"Claude Code был прерван внешним сигналом (rc={last_rc}) "
            f"после повторной попытки. Возможно, OOM-killer или активный "
            f"deploy. Сценарий сохранён — можно повторить вручную."
        )
    if last_rc != 0:
        raise HyperFramesBrollError(
            f"Claude Code упал (rc={last_rc}): {(proc.stderr if proc else '')[:500]}"
        )
    # Парсинг stream-json: stdout — поток JSONL-событий (init, assistant,
    # user (tool_result), result). Финальное `type=result` содержит
    # total_cost_usd / num_turns / result. Заодно — статистика шагов
    # (Read/Edit/Write/Bash) для понимания что Claude делал. Единый парсер
    # `_parse_stream` (используется и в timeout-ветке) — один источник правды.
    cost_usd = 0.0
    diag = _parse_stream(proc.stdout or "")
    tool_counts = diag["tool_counts"]
    result_event = diag["result_event"]
    num_events = diag["num_events"]

    if result_event is not None:
        # total_cost_usd может прилететь как число, числовая строка ("0.42"),
        # или нечисловое значение ("N/A", null) — последнее ловим в except.
        _raw_cost = result_event.get("total_cost_usd", 0.0)
        try:
            cost_usd = float(_raw_cost) if _raw_cost is not None else 0.0
        except (TypeError, ValueError):
            logger.warning(f"[hf_broll] total_cost_usd не число: {_raw_cost!r}, считаю 0")
            cost_usd = 0.0
        turns = result_event.get("num_turns")
        result_snip = (result_event.get("result") or "")[:300]
        tools_summary = ", ".join(
            f"{k}={v}" for k, v in sorted(tool_counts.items(), key=lambda x: -x[1])
        ) or "—"
        logger.info(
            f"[hf_broll] Claude Code: cost=${cost_usd:.4f}, "
            f"turns={turns}, events={num_events}, tools=[{tools_summary}], "
            f"result={result_snip!r}"
        )
    else:
        logger.warning(
            f"[hf_broll] в stream-json не найден type=result "
            f"(events={num_events}); stdout head: {(proc.stdout or '')[:200]!r}"
        )
    return cost_usd


# ── Рендер ───────────────────────────────────────────────────────────
def _render_env() -> dict:
    """Окружение для рендера: путь к chrome-headless-shell обязателен."""
    env = dict(os.environ)
    browser = _resolve_browser_path()
    if browser:
        env["HYPERFRAMES_BROWSER_PATH"] = browser
    else:
        logger.warning(
            "[hf_broll] chrome-headless-shell не найден — рендер может "
            "подхватить системный snap-chromium и упасть. "
            "См. project_maksim_dual_broll_engine.md."
        )
    env.setdefault("HOME", str(Path(HF_PROJECT).parent))
    return env


def _render(scene_file: str, out_path: Path) -> tuple[bool, str]:
    """Рендерит одну композицию. → (успех, текст_ошибки)."""
    try:
        proc = subprocess.run(
            [
                "nice", "-n", "15",
                "npx", "--yes", f"hyperframes@{HF_VERSION}", "render",
                "-c", scene_file,
                "-o", str(out_path),
            ],
            cwd=HF_PROJECT, env=_render_env(),
            capture_output=True, text=True, timeout=RENDER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"{scene_file}: таймаут рендера"
    if proc.returncode != 0 or not out_path.exists():
        tail = (proc.stderr or proc.stdout)[-700:]
        return False, f"{scene_file}: {tail}"
    return True, ""


def _render_all(out_dir: Path) -> tuple[list[Path], list[str]]:
    """Рендерит все 6 scene-композиций. → (готовые_клипы, ошибки).

    Namespace `out_dir/hyperframes/hf_NN.mp4` — отдельный от Remotion
    (`out_dir/autobroll/auto_NN.mp4`), чтобы video_assembler мог выбрать
    источник по движку.
    """
    hf_dir = out_dir / "hyperframes"
    hf_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    errors: list[str] = []
    for i, scene_file in enumerate(SCENE_FILES, start=1):
        if not (HF_PROJECT / scene_file).exists():
            errors.append(f"{scene_file}: файл не создан Клодом")
            continue
        out_path = hf_dir / f"hf_{i:02d}.mp4"
        ok, err = _render(scene_file, out_path)
        if ok:
            clips.append(out_path)
        else:
            errors.append(err)
            logger.warning(f"[hf_broll] рендер {scene_file} не удался: {err[:200]}")
    return clips, errors


# ── Layout-инспекция (свой детектор, см. hf_inspect_layout.mjs) ───────
LAYOUT_INSPECTOR = "hf_inspect_layout.mjs"
# 30s ≈ ×1.5 запаса над типичным ~10-20s/сцена (замеры 1 июня). Старое 120
# давало бюджет 120×6 сцен × 3 раунда = 36 минут только на инспекцию,
# заметно дольше самой генерации (агент-ревью 1 июня, MEDIUM).
LAYOUT_INSPECT_TIMEOUT = 30


def _inspect_layout(scene_file: str) -> list[dict]:
    """Гоняет node-детектор на одной сцене → список layout-issues.

    Пустой список = чисто. На ЛЮБОЙ ошибке запуска детектора (нет node,
    нет браузера, таймаут, не распарсился JSON) — пустой список: детектор
    QUALITY-инструмент, он НЕ должен блокировать генерацию видео.

    Детектор exit-коды: 0=ok, 1=есть issues, 2=ошибка запуска. stdout —
    JSON в случаях 0/1.
    """
    inspector = HF_PROJECT / LAYOUT_INSPECTOR
    if not inspector.exists():
        logger.warning(f"[hf_broll] детектор {LAYOUT_INSPECTOR} не найден — пропускаю layout-проверку")
        return []
    try:
        proc = subprocess.run(
            ["node", str(inspector), str(HF_PROJECT / scene_file)],
            cwd=HF_PROJECT, env=_render_env(),
            capture_output=True, text=True, timeout=LAYOUT_INSPECT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"[hf_broll] layout-inspect таймаут на {scene_file}")
        return []
    except Exception as e:
        logger.warning(f"[hf_broll] layout-inspect не запустился: {e}")
        return []
    try:
        data = json.loads(proc.stdout)
        return data.get("issues", []) or []
    except Exception:
        if proc.returncode == 2:
            logger.warning(f"[hf_broll] layout-inspect error на {scene_file}: {(proc.stderr or '')[:200]}")
        return []


def _inspect_all_scenes() -> dict:
    """{scene_file: [issues]} — только для сцен, где детектор нашёл проблемы."""
    out: dict[str, list] = {}
    for sf in SCENE_FILES:
        if not (HF_PROJECT / sf).exists():
            continue
        issues = _inspect_layout(sf)
        if issues:
            out[sf] = issues
    return out


def _format_layout_issues(by_scene: dict) -> str:
    """Человекочитаемый фидбек для Клода с координатами — для fix-round."""
    lines = [
        "ПРОБЛЕМЫ ВЁРСТКИ (детектор layout, кадр 1080×1920). Исправь композицию:",
    ]
    for sf, issues in by_scene.items():
        lines.append(f"\n{sf}:")
        for it in issues[:8]:
            t = it.get("type")
            if t == "offscreen":
                lines.append(
                    f"  • {it.get('kind','элемент')} «{it.get('text','')}» уходит за край "
                    f"({it.get('edge')}), бокс {it.get('rect')}. Удержи ВЕСЬ контент в "
                    f"safe-area x∈[40,1040], y∈[480,1440]."
                )
            elif t == "overlap":
                lines.append(
                    f"  • тексты «{it.get('a')}» и «{it.get('b')}» НАЛЕЗАЮТ друг на друга "
                    f"({it.get('overlapPx')}px²). Разнеси их — flex-column с gap, не "
                    f"absolute-координаты."
                )
            elif t == "crowding":
                lines.append(
                    f"  • «{it.get('a')}» и «{it.get('b')}» слишком ТЕСНО "
                    f"(зазор {it.get('gapPx')}px). Увеличь вертикальный отступ до ≥40px."
                )
    return "\n".join(lines)


# ── Главный оркестратор ──────────────────────────────────────────────
def _notify(progress_cb, text: str) -> None:
    """Fire-and-forget прогресс-уведомление (10 июня).

    22-минутная генерация без единого апдейта выглядела для Артёма как
    «не сделался». Сбой callback'а НИКОГДА не валит пайплайн."""
    if not progress_cb:
        return
    try:
        progress_cb(text)
    except Exception as e:
        logger.warning(f"[hf_broll] progress_cb упал (игнорирую): {e}")


def generate_hyperframes_broll(
    script_text: str, out_dir: str | Path,
    progress_cb=None,
) -> tuple[list[Path], float]:
    """Сценарий → 6 готовых hf_01..06.mp4 в out_dir/hyperframes/.

    progress_cb: опциональный callable(str) — фазовые апдейты для юзера
    (вызывается из рабочего потока; в боте мостится через
    run_coroutine_threadsafe).

    Возвращает (список_клипов, total_cost_usd). Тот же контракт, что
    generate_auto_broll (Remotion) — бот выбирает движок.

    Бросает HyperFramesBrollError, если не удалось даже после повторов.
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if not HF_PROJECT.exists():
        raise HyperFramesBrollError(f"Нет проекта HyperFrames: {HF_PROJECT}")

    if not _GEN_LOCK.acquire(blocking=False):
        raise HyperFramesBrollError(
            "Генерация графики (HyperFrames) уже идёт для другого ролика — "
            "подожди ~10 минут и повтори."
        )
    total_cost = 0.0
    try:
        ensure_git_baseline()

        # ── ФАЗА 1: storyboard (machine-gated diversity) ─────────────────
        # Claude планирует 6 РАЗНЫХ сцен → storyboard.json → валидатор-гейт.
        # Это решает монотонность по построению (см. project_hyperframes_
        # pipeline_architecture.md).
        logger.info("[hf_broll] Фаза 1: storyboard (раскадровка 6 сцен)…")
        _notify(progress_cb, "📋 Шаг 1/3: придумываю раскадровку (6 разных сцен)…")
        storyboard, sb_cost = _run_storyboard_phase(script_text)
        total_cost += sb_cost
        _notify(progress_cb,
                "🎨 Шаг 2/3: раскадровка готова — пишу 6 HTML-сцен (~3-5 мин)…")

        # ── ФАЗА 2: per-scene build ────────────────────────────────────
        # По умолчанию (10 июня) — SINGLE-SHOT: одна completion на сцену без
        # инструментов (SubscriptionClient), файл пишет Python. Агентный
        # headless-build зависал 18/18 (Write вне workspace → permission-тупик
        # в `claude -p`). См. _run_build_phase_singleshot.
        #   HF_AGENT_BUILD=1  → агентный SceneScheduler (Phase 1 Step 6, отладка)
        #   HF_LEGACY_BUILD=1 → старый последовательный agent-flow
        legacy_build = os.getenv("HF_LEGACY_BUILD", "").strip() == "1"
        agent_build = os.getenv("HF_AGENT_BUILD", "").strip() == "1"
        if legacy_build:
            logger.info("[hf_broll] Фаза 2: legacy последовательный build (HF_LEGACY_BUILD=1)")
            total_cost += _run_build_phase(storyboard)
        else:
            import asyncio as _asyncio
            from job_context import JobContext as _JobContext
            runs_root = Path(os.getenv("HF_RUNS_ROOT", str(HF_PROJECT.parent / "runs")))
            job = _JobContext.create(script_text, runs_root)
            logger.info(f"[hf_broll] job workspace: {job.root}")
            job.write_storyboard(storyboard)
            if agent_build:
                logger.info("[hf_broll] Фаза 2: агентный async-build (HF_AGENT_BUILD=1)")
                total_cost += _asyncio.run(_run_build_phase_async(storyboard, job))
            else:
                logger.info("[hf_broll] Фаза 2: single-shot параллельный build")
                _clear_scene_files()
                total_cost += _asyncio.run(
                    _run_build_phase_singleshot(storyboard, job))

        # Motion smoke-test gate (Phase 1 Step 6, по ревью GPT) — ловит
        # scene_04-style баги (timeline есть, но визуально статично) ДО
        # того как рендер сожрёт минуты. HF_SKIP_MOTION_GATE=1 → пропустить.
        if not os.getenv("HF_SKIP_MOTION_GATE", "").strip() == "1":
            try:
                _run_motion_gate()
                logger.info("[hf_broll] motion gate: все 6 сцен прошли")
            except HyperFramesBrollError as e:
                logger.error(f"[hf_broll] motion gate fail: {e}")
                raise

        clips: list[Path] = []
        errors: list[str] = []
        fix_round = 0
        # Выбор render-движка (Phase 1 Step 4 — наш по умолчанию, npx fallback)
        use_npx_render = os.getenv("HF_USE_NPX_RENDER", "").strip() == "1"
        _render_fn = _render_all if use_npx_render else _render_all_native
        if use_npx_render:
            logger.info("[hf_broll] render: npx hyperframes (HF_USE_NPX_RENDER=1)")
        _notify(progress_cb,
                "🎬 Шаг 3/3: сцены написаны — рендерю видео (~4-5 мин)…")
        while True:
            # 1) Layout-инспекция (дешевле рендера — ловим overlap/offscreen
            #    ДО траты времени на рендер). 2) Рендер.
            layout_by_scene = _inspect_all_scenes()
            clips, errors = _render_fn(out_dir)

            # Render-ошибки ФАТАЛЬНЫ (без них нет видео). Layout — QUALITY.
            problems: list[str] = []
            if layout_by_scene:
                problems.append(_format_layout_issues(layout_by_scene))
            if errors:
                problems.append("ОШИБКИ РЕНДЕРА:\n" + "\n".join(errors))

            if not problems:
                break  # всё чисто — выходим

            if fix_round >= MAX_FIX_ROUNDS:
                # Раунды исчерпаны.
                if errors:
                    raise HyperFramesBrollError(
                        f"B-roll (HyperFrames) не собрался после {MAX_FIX_ROUNDS} "
                        f"повторов. Ошибки: {'; '.join(e[:120] for e in errors)}"
                    )
                # Рендер успешен, но layout-проблемы остались — НЕ блокируем,
                # отдаём с предупреждением (лучше неидеальное видео, чем ничего).
                logger.warning(
                    f"[hf_broll] layout-проблемы не вычищены за {MAX_FIX_ROUNDS} "
                    f"раундов, отдаю как есть: "
                    f"{ {k: len(v) for k, v in layout_by_scene.items()} }"
                )
                break

            fix_round += 1
            n_layout = sum(len(v) for v in layout_by_scene.values())
            logger.info(
                f"[hf_broll] fix-round {fix_round}: "
                f"{len(errors)} render-ошибок, {n_layout} layout-проблем"
            )
            _notify(progress_cb,
                    f"🔧 Доводка вёрстки {fix_round}/{MAX_FIX_ROUNDS}: детектор "
                    f"нашёл {n_layout} замечаний — перегенерирую проблемные "
                    f"сцены и рендерю заново (~5-7 мин)…")
            if legacy_build:
                # старый монолитный агент-фиксер (весь проект одним вызовом)
                total_cost += _run_claude(
                    _build_prompt(script_text, fix_error="\n\n".join(problems))
                )
                _revert_stray()
            else:
                # single-shot: перегенерация ТОЛЬКО проблемных сцен
                _fix_scenes_singleshot(
                    storyboard, _problems_by_scene(layout_by_scene, errors)
                )
    finally:
        _GEN_LOCK.release()

    logger.info(
        f"[hf_broll] ✅ готово: {len(clips)} вставок в {out_dir}/hyperframes, "
        f"Claude итого: ${total_cost:.4f}"
    )
    return clips, total_cost


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _out = sys.argv[1] if len(sys.argv) > 1 else "_hf_test"
    _script = sys.stdin.read().strip()
    if not _script:
        print("FAIL: пустой сценарий на stdin")
        sys.exit(1)
    try:
        clips, cost_usd = generate_hyperframes_broll(_script, _out)
        print(f"OK: {len(clips)} вставок, Claude cost: ${cost_usd:.4f}")
        for c in clips:
            print(f"  {c} — {c.stat().st_size // 1024} KB")
    except HyperFramesBrollError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
