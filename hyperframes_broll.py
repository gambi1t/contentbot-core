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
from pathlib import Path

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
CLAUDE_TIMEOUT = 900      # сек на одну сессию Claude Code (HTML для 6 сцен)
RENDER_TIMEOUT = 300      # сек на рендер одной вставки
MAX_FIX_ROUNDS = 2        # сколько раз просим Клода починить
HF_VERSION = "0.6.56"     # пин версии CLI (как в package.json проекта)


class HyperFramesBrollError(Exception):
    """Не удалось сгенерировать B-roll через HyperFrames даже после повторов."""


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
def _build_prompt(script_text: str, fix_error: str | None = None) -> str:
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
    return f"""Ты — моушн-дизайнер студии. Создай 6 коротких графических
B-roll-вставок под сценарий ролика для Telegram-канала предпринимателя
(картинг + глэмпинг Life Drive, Тюмень), используя HyperFrames.

СЦЕНАРИЙ (озвучка аватара, ~30 секунд):
─────────────────────────────────────
{script_text}
─────────────────────────────────────

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
- Весь экшен — по центру кадра (B-roll показывается в средней зоне поверх
  говорящей головы). Крупный читаемый текст.
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
    """Откатывает все правки Клода КРОМЕ scene_NN.html."""
    if not (HF_PROJECT / ".git").exists():
        return
    changed = _git(["diff", "--name-only"]).stdout.split()
    stray = [f for f in changed if f not in SCENE_FILES]
    if stray:
        _git(["checkout", "--", *stray])
        logger.warning(f"[hf_broll] откатил лишние правки: {stray}")
    # снимок сцен — текущая версия становится новым базлайном
    _git(["add", *SCENE_FILES])
    _git(["commit", "-m", "scenes update", "--allow-empty"])


# ── Claude Code ──────────────────────────────────────────────────────
def _run_claude(prompt: str) -> float:
    """Запускает Claude Code; возвращает total_cost_usd из json-ответа."""
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
    proc = subprocess.run(
        [
            "claude", "-p", prompt,
            "--allowedTools", "Read,Edit,Write,Glob,Grep",
            "--output-format", "json",
        ],
        cwd=HF_PROJECT, env=env,
        capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise HyperFramesBrollError(
            f"Claude Code упал (rc={proc.returncode}): {proc.stderr[:500]}"
        )
    cost_usd = 0.0
    try:
        data = json.loads(proc.stdout)
        cost_usd = float(data.get("total_cost_usd", 0.0))
        result_snip = (data.get("result") or "")[:300]
        logger.info(
            f"[hf_broll] Claude Code: cost=${cost_usd:.4f}, "
            f"turns={data.get('num_turns')}, result={result_snip!r}"
        )
    except Exception as e:
        logger.warning(
            f"[hf_broll] не смог распарсить JSON ответа Claude: {e}; "
            f"stdout head: {proc.stdout[:200]!r}"
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


# ── Главный оркестратор ──────────────────────────────────────────────
def generate_hyperframes_broll(
    script_text: str, out_dir: str | Path,
) -> tuple[list[Path], float]:
    """Сценарий → 6 готовых hf_01..06.mp4 в out_dir/hyperframes/.

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

        logger.info("[hf_broll] Claude Code генерирует 6 scene-композиций…")
        total_cost += _run_claude(_build_prompt(script_text))
        _revert_stray()

        clips, errors = _render_all(out_dir)

        fix_round = 0
        while errors and fix_round < MAX_FIX_ROUNDS:
            fix_round += 1
            logger.info(
                f"[hf_broll] чиню (попытка {fix_round}): {len(errors)} ошибок"
            )
            total_cost += _run_claude(
                _build_prompt(script_text, fix_error="\n".join(errors))
            )
            _revert_stray()
            clips, errors = _render_all(out_dir)

        if errors:
            raise HyperFramesBrollError(
                f"B-roll (HyperFrames) не собрался после {MAX_FIX_ROUNDS} "
                f"повторов. Ошибки: {'; '.join(e[:120] for e in errors)}"
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
