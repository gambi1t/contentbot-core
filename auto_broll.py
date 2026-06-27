"""auto_broll.py — автономная генерация графических B-roll-вставок.

Цепочка: сценарий → Claude Code на сервере переписывает
`src/scenes/AutoBroll.tsx` → компиляция/рендер 6 вставок → broll_01..06.mp4.

Защита: откат лишних правок (git), повтор с передачей ошибки Клоду,
жёсткий лимит попыток. Без участия человека.

Можно запускать standalone для теста:
    python auto_broll.py "_montage_test3" < script.txt
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# AutoBroll.tsx — общий файл проекта Remotion. Два параллельных прогона
# затрут правки друг друга, поэтому генерация строго последовательная.
_GEN_LOCK = threading.Lock()

# ── Пути и константы ─────────────────────────────────────────────────
BROLL_PROJECT = Path(
    os.getenv("BROLL_PROJECT_DIR", "/home/maksim-bot/panferov-broll")
)
AUTOBROLL_REL = "src/scenes/AutoBroll.tsx"
N_INSERTS = 6
COMP_IDS = [f"AutoBroll{i}" for i in range(1, N_INSERTS + 1)]
CLAUDE_TIMEOUT = 720      # сек на одну сессию Claude Code
RENDER_TIMEOUT = 360      # сек на рендер одной вставки
MAX_FIX_ROUNDS = 2        # сколько раз просим Клода починить


class AutoBrollError(Exception):
    """Не удалось сгенерировать B-roll даже после повторов."""


# ── Палитра и контекст активного тенанта (de-Maksim, срез C) ──────────
def _palette_line() -> str:
    """Инструкция по палитре для Claude (Option B): цвета — ТОЛЬКО через
    env-driven токен colors.* из ../fonts; hex-литералы ЗАПРЕЩЕНЫ, иначе
    per-tenant env-инъекция при рендере обходится (panferov получит оранж
    вместо azure). Палитра впрыскивается per-tenant через REMOTION_*-env
    (см. _palette_env), поэтому здесь — правило, а не конкретные цвета."""
    return (
        "ВСЕ цвета — ТОЛЬКО через colors.* из ../fonts (colors.bg / colors.card / "
        "colors.accent / colors.accentDim / colors.text / colors.textDim / "
        "colors.border). НЕ вписывай hex-литералы цветов (#rrggbb) — палитра "
        "приходит per-tenant из env при рендере; зашитый hex её перебьёт."
    )


def _business_context() -> str:
    """Контекст автора активного тенанта (panferov → Артём/AI, иначе → Максим).
    Переиспользуем готовый per-tenant резолвер из ai_video_broll (Rule №2)."""
    try:
        from ai_video_broll import _default_persona
        return _default_persona()
    except Exception as e:
        logger.warning(f"[auto_broll] контекст автора недоступен: {e}")
        return ""


def _darken(hex_color: str, factor: float = 0.72) -> str:
    """Затемнить hex (для accentDim, которого нет в контракте)."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    return "#%02X%02X%02X" % (int(r * factor), int(g * factor), int(b * factor))


def _lighten(hex_color: str, amt: int = 0x18) -> str:
    """Осветлить hex аддитивно (для border — рамка видимо светлее фона/подложки)."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    return "#%02X%02X%02X" % (min(255, r + amt), min(255, g + amt), min(255, b + amt))


def _palette_env() -> dict:
    """REMOTION_*-env с палитрой активного тенанта — впрыскивается в рендер
    ОБЩЕГО Node-проекта (Option B: один проект, палитра per-tenant из
    style_contract, как HyperFrames). Заполняется для ЛЮБОГО тенанта с
    контрактом: default → style_contract.json (оранж Максима), panferov →
    style_contract.panferov.json (Nox Dark azure). Пусто ТОЛЬКО если контракт-
    файла нет — тогда Node берёт дефолты токенов (тоже оранж).
    accentDim/border нет в контракте → производные (затемнённый accent /
    осветлённая подложка, чтобы рамка не сливалась с фоном)."""
    try:
        from style_contract import load_style_contract
        p = load_style_contract()["palette"]
        accent = p["accent"]
        return {
            "REMOTION_BG": p["bg_primary"],
            "REMOTION_CARD": p["bg_secondary"],
            "REMOTION_ACCENT": accent,
            "REMOTION_ACCENT_DIM": _darken(accent),
            "REMOTION_TEXT": p["text_primary"],
            "REMOTION_TEXT_DIM": p["text_muted"],
            "REMOTION_BORDER": _lighten(p["bg_secondary"]),
        }
    except Exception as e:
        logger.warning(f"[broll] palette-env недоступна, дефолт проекта: {e}")
        return {}


# ── Anthropic-ключ ───────────────────────────────────────────────────
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
        raise AutoBrollError("Нет ANTHROPIC_API_KEY (env или .env).")
    return key


# ── Промпт для Claude Code ───────────────────────────────────────────
def _build_prompt(script_text: str, fix_error: str | None = None) -> str:
    if fix_error:
        return (
            "Файл src/scenes/AutoBroll.tsx, который ты только что записал, "
            "НЕ собирается или падает при рендере. Ошибка:\n\n"
            f"{fix_error}\n\n"
            "Исправь src/scenes/AutoBroll.tsx так, чтобы он компилировался и "
            "рендерился. Не меняй другие файлы. Сохрани 6 экспортов "
            "Auto1…Auto6."
        )
    palette = _palette_line()
    _ctx = _business_context()
    ctx_block = f"\nКОНТЕКСТ АВТОРА: {_ctx}\n" if _ctx else ""
    return f"""Ты — моушн-дизайнер студии Постулат. Перепиши ЦЕЛИКОМ файл
`src/scenes/AutoBroll.tsx` — 6 коротких графических B-roll-вставок под
сценарий ролика для Telegram-канала.
{ctx_block}
СЦЕНАРИЙ (озвучка аватара, ~30 секунд):
─────────────────────────────────────
{script_text}
─────────────────────────────────────

ЧТО СДЕЛАТЬ:
1. Прочитай эталон `src/scenes/MaksimInserts2.tsx` — там ВСЕ конвенции
   стиля, структуры и анимации. Следуй им буквально (helpers Ambient,
   Band, Label; центральная полоса; быстрый билд).
2. Раздели сценарий на 6 визуальных моментов в хронологическом порядке.
3. Перепиши `src/scenes/AutoBroll.tsx`: компоненты Auto1…Auto6, где
   Auto1 — первый момент сценария, Auto6 — последний.

ПРАВИЛА (нарушать нельзя):
- Файл экспортирует РОВНО: Auto1, Auto2, Auto3, Auto4, Auto5, Auto6.
- Каждая вставка — 120 кадров @ 30fps, кадр 1080×1920.
- Весь экшен — в центральной полосе 1080×960 (band y∈[480,1440]),
  через helper Band. Ничего не выносить за полосу.
- Полный визуал вставки выходит за ~1 секунду (≈кадр 30) и дальше
  держится — сегмент монтажа короткий.
- {palette} Шрифт Inter Tight.
  Импорт: import {{ interTight, jetBrainsMono, colors }} from "../fonts".
- Только графика и моушн-дизайн: счётчики, графики, диаграммы, карточки,
  чек-листы. НЕ изображать людей, лица, руки, силуэты.
- Каждая вставка иллюстрирует КОНКРЕТНЫЙ момент сценария — то, о чём
  аватар говорит в эту секунду.
- Никаких выдуманных точных денежных цифр о бизнесе (выручка/прибыль в
  рублях). Иллюстративные числа и проценты — допустимы.
- Текст на вставках — короткий и крупный, читается с телефона.
- ЧИТАЕМОСТЬ ПРЕВЫШЕ ВСЕГО. Любой текст должен легко читаться.
- Перечёркивание (линия поверх текста) — ТОЛЬКО через короткое слово
  или значение (1-2 слова), сам текст под линией остаётся читаемым.
  НЕ перечёркивай длинные фразы и предложения — это делает их
  нечитаемыми. Чтобы показать «отвергнуто/неверно/в прошлом», используй
  тусклый цвет (colors.textDim) и значок ✕ рядом — НЕ линию через весь
  текст.

ОГРАНИЧЕНИЯ:
- Редактируй ТОЛЬКО `src/scenes/AutoBroll.tsx`. НЕ трогай Root.tsx,
  fonts, MaksimInserts*.tsx и другие файлы.
- Не меняй имена/количество экспортов.

После записи файла — закончи. Компиляцию и рендер сделает оркестратор."""


# ── git-базлайн и откат лишних правок ────────────────────────────────
def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=BROLL_PROJECT,
        capture_output=True, text=True, timeout=60,
    )


def ensure_git_baseline() -> None:
    """Инициализирует git в проекте Remotion (один раз), чтобы можно
    было откатывать лишние правки Клода."""
    if (BROLL_PROJECT / ".git").exists():
        return
    _git(["init"])
    _git(["config", "user.email", "bot@maksim-bot"])
    _git(["config", "user.name", "maksim-bot"])
    _git(["add", "-A"])
    _git(["commit", "-m", "baseline"])
    logger.info("[auto_broll] git baseline создан")


def _revert_stray() -> None:
    """Откатывает все правки Клода КРОМЕ AutoBroll.tsx."""
    if not (BROLL_PROJECT / ".git").exists():
        return
    changed = _git(["diff", "--name-only"]).stdout.split()
    stray = [f for f in changed if f != AUTOBROLL_REL]
    if stray:
        _git(["checkout", "--", *stray])
        logger.warning(f"[auto_broll] откатил лишние правки: {stray}")
    # снимок AutoBroll.tsx — текущая версия становится новым базлайном
    _git(["add", AUTOBROLL_REL])
    _git(["commit", "-m", "autobroll update", "--allow-empty"])


# ── Claude Code ──────────────────────────────────────────────────────
def _run_claude(prompt: str) -> float:
    """Запускает Claude Code; возвращает total_cost_usd из json-ответа."""
    env = dict(os.environ)
    # Авторизация Claude Code. Приоритет в самом CLI: ANTHROPIC_API_KEY >
    # CLAUDE_CODE_OAUTH_TOKEN. Поэтому если задан OAuth-токен подписки
    # (Max Артёма) — УБИРАЕМ API-ключ из окружения дочернего процесса, иначе
    # подписка не подхватится и пойдёт метеред-биллинг. Нет токена → фолбэк
    # на API-ключ (поведение по умолчанию).
    oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if oauth_token:
        env.pop("ANTHROPIC_API_KEY", None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
        logger.info("[auto_broll] Claude Code auth: подписка (CLAUDE_CODE_OAUTH_TOKEN)")
    else:
        env["ANTHROPIC_API_KEY"] = _anthropic_key()
        logger.info("[auto_broll] Claude Code auth: API-ключ (метеред)")
    env.setdefault("HOME", str(Path(BROLL_PROJECT).parent))
    proc = subprocess.run(
        [
            "claude", "-p", prompt,
            "--allowedTools", "Read,Edit,Write,Glob,Grep",
            "--output-format", "json",
        ],
        cwd=BROLL_PROJECT, env=env,
        capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise AutoBrollError(
            f"Claude Code упал (rc={proc.returncode}): {proc.stderr[:500]}"
        )
    # Claude Code -p --output-format json возвращает {result, total_cost_usd,
    # usage:{input_tokens, output_tokens, ...}, num_turns, ...}. Парсим cost.
    cost_usd = 0.0
    try:
        data = json.loads(proc.stdout)
        cost_usd = float(data.get("total_cost_usd", 0.0))
        result_snip = (data.get("result") or "")[:300]
        logger.info(
            f"[auto_broll] Claude Code: cost=${cost_usd:.4f}, "
            f"turns={data.get('num_turns')}, result={result_snip!r}"
        )
    except Exception as e:
        logger.warning(
            f"[auto_broll] не смог распарсить JSON ответа Claude: {e}; "
            f"stdout head: {proc.stdout[:200]!r}"
        )
    return cost_usd


# ── Рендер ───────────────────────────────────────────────────────────
def _render(comp_id: str, out_path: Path) -> tuple[bool, str]:
    """Рендерит одну композицию. → (успех, текст_ошибки)."""
    try:
        proc = subprocess.run(
            [
                "nice", "-n", "15",
                "npx", "remotion", "render", comp_id, str(out_path),
            ],
            cwd=BROLL_PROJECT,
            env={**os.environ, **_palette_env()},  # палитра тенанта в общий проект
            capture_output=True, text=True, timeout=RENDER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"{comp_id}: таймаут рендера"
    if proc.returncode != 0 or not out_path.exists():
        tail = (proc.stderr or proc.stdout)[-700:]
        return False, f"{comp_id}: {tail}"
    return True, ""


def _render_all(out_dir: Path) -> tuple[list[Path], list[str]]:
    """Рендерит все 6 AutoBroll-композиций. → (готовые_клипы, ошибки).

    W1 (27 May 2026): namespace separation. Раньше писал в
    `out_dir/broll_NN.mp4` — тот же namespace что и SMM-загрузки через
    «📥 Готовые материалы». При сборке всё бралось в кучу. Теперь AI-вставки
    идут в `out_dir/autobroll/auto_NN.mp4` — отдельная папка, `_find_broll`
    в video_assembler.py выбирает источник по mode.
    """
    autobroll_dir = out_dir / "autobroll"
    autobroll_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    errors: list[str] = []
    for i, comp_id in enumerate(COMP_IDS, start=1):
        out_path = autobroll_dir / f"auto_{i:02d}.mp4"
        ok, err = _render(comp_id, out_path)
        if ok:
            clips.append(out_path)
        else:
            errors.append(err)
            logger.warning(f"[auto_broll] рендер {comp_id} не удался: {err[:200]}")
    return clips, errors


# ── Главный оркестратор ──────────────────────────────────────────────
def generate_auto_broll(
    script_text: str, out_dir: str | Path,
) -> tuple[list[Path], float]:
    """Сценарий → 6 готовых broll_01..06.mp4 в out_dir.

    Возвращает (список_клипов, total_cost_usd) — Claude Code суммирует
    стоимость основного прогона + всех fix-повторов.

    Бросает AutoBrollError, если не удалось даже после повторов.
    """
    # Абсолютный путь: рендер идёт с cwd=BROLL_PROJECT, относительный
    # путь ушёл бы не туда. resolve() делает его абсолютным.
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if not BROLL_PROJECT.exists():
        raise AutoBrollError(f"Нет проекта Remotion: {BROLL_PROJECT}")

    # Один прогон за раз — AutoBroll.tsx общий для всех роликов (in-process).
    if not _GEN_LOCK.acquire(blocking=False):
        raise AutoBrollError(
            "Генерация графики уже идёт для другого ролика — "
            "подожди ~10 минут и повтори."
        )
    # Межпроцессный flock: один OAuth-токен подписки шарится между процессами
    # (deep-research, Cursor, второй бот, HyperFrames). Сериализуем тяжёлые
    # генерации хотя бы межпроцессно (Fix 6 / Critical 3, дешёвая часть).
    from claude_gen_lock import acquire_gen_flock, release_gen_flock, ClaudeGenBusy
    try:
        _flock = acquire_gen_flock("auto_broll")
    except ClaudeGenBusy as e:
        _GEN_LOCK.release()
        raise AutoBrollError(str(e))
    total_cost = 0.0
    try:
        ensure_git_baseline()

        logger.info("[auto_broll] Claude Code генерирует AutoBroll.tsx…")
        total_cost += _run_claude(_build_prompt(script_text))
        _revert_stray()

        clips, errors = _render_all(out_dir)

        fix_round = 0
        while errors and fix_round < MAX_FIX_ROUNDS:
            fix_round += 1
            logger.info(
                f"[auto_broll] чиню (попытка {fix_round}): {len(errors)} ошибок"
            )
            total_cost += _run_claude(
                _build_prompt(script_text, fix_error="\n".join(errors))
            )
            _revert_stray()
            clips, errors = _render_all(out_dir)

        if errors:
            raise AutoBrollError(
                f"B-roll не собрался после {MAX_FIX_ROUNDS} повторов. "
                f"Ошибки: {'; '.join(e[:120] for e in errors)}"
            )
    finally:
        release_gen_flock(_flock)
        _GEN_LOCK.release()

    logger.info(
        f"[auto_broll] ✅ готово: {len(clips)} вставок в {out_dir}, "
        f"Claude итого: ${total_cost:.4f}"
    )
    return clips, total_cost


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _out = sys.argv[1] if len(sys.argv) > 1 else "_montage_test3"
    _script = sys.stdin.read().strip()
    if not _script:
        print("FAIL: пустой сценарий на stdin")
        sys.exit(1)
    try:
        clips, cost_usd = generate_auto_broll(_script, _out)
        print(f"OK: {len(clips)} вставок, Claude cost: ${cost_usd:.4f}")
        for c in clips:
            print(f"  {c} — {c.stat().st_size // 1024} KB")
    except AutoBrollError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
