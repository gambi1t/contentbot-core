"""screen_broll.py — генерация экранов кода/UI/терминала (Remotion, Path B).

Цепочка: тема/сценарий → Claude отдаёт JSON-пропсы под готовый Remotion-шаблон
(AiToolDeepDive — терминал/код) → рендер композиции с `--props` → mp4.

Отличие от auto_broll (моушн-вставки): Claude НЕ пишет код, только структурированный
JSON-контент. Поэтому:
  - нет self-heal-цикла компиляции (нечему ломаться — рендерим готовую композицию);
  - нет общего мутабельного файла (AutoBroll.tsx) → прогоны независимы и параллельны;
  - цвета берёт сам шаблон (перекрашен в Nox Dark, Ф0), пропсы несут ТОЛЬКО контент,
    поэтому бренд-палитра сюда не протекает (страж test_core_no_brand_leak).

Standalone-тест:
    python screen_broll.py "_screens_test" "Claude Code умеет писать MVP за минуты"
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

# Переиспользуем единый источник пути проекта и ключа (не плодим свой).
from auto_broll import BROLL_PROJECT, _anthropic_key  # noqa: E402

logger = logging.getLogger(__name__)

CLAUDE_TIMEOUT = 300   # сек на генерацию JSON-пропсов
RENDER_TIMEOUT = 360   # сек на рендер одной композиции

_TOOL_STYLES = ("block", "glitch", "stencil", "neon")


class ScreenBrollError(Exception):
    """Не удалось сгенерировать экран-вставку."""


# ── Реестр шаблонов ──────────────────────────────────────────────────
# Каждый шаблон: composition_id по умолчанию (из Root.tsx), обязательные поля
# пропсов и текст-спека полей для промпта Claude.
TEMPLATES: dict[str, dict] = {
    "AiToolDeepDive": {
        "comp_id": "AiToolDeepDive-ClaudeCode",
        "required": ["toolName", "promptText"],
        "title": "разбор/тутор/use-case AI-инструмента (терминал-prompt + опц. output)",
        "spec": (
            "- toolName: имя инструмента крупными буквами (например \"CLAUDE CODE\", \"CURSOR\")\n"
            "- toolNameStyle: один из \"block\" | \"glitch\" | \"stencil\" | \"neon\"\n"
            "- tagBadge: короткий тег сверху (например \"★ РАЗБОР\", \"★ DEEP DIVE\")\n"
            "- promptPrefix: префикс перед промптом, обычно \">\"\n"
            "- promptText: одна строка — реалистичный промпт к инструменту\n"
            "- outputLines: 0-4 КОРОТКИХ строки вывода терминала, каждая начинается с \"✓\", \"✗\" или \"$\"\n"
            "- outroLine: финальная строка — URL/CTA (например \"claude.ai/code\")"
        ),
    },
    # AiProductLaunch (2-й шаблон-экран Path B) добавится отдельным шагом Ф2+:
    # с собственной нормализацией в _validate_props, тестом и E2E. Сейчас НЕ
    # регистрируем — иначе непереданные Claude поля молча наследуются из
    # defaultProps композиции (EXAMPLE_CLAUDE_OPUS) → чужой контент на экране.
}


# ── Промпт ───────────────────────────────────────────────────────────
def _build_props_prompt(theme: str, template_key: str) -> str:
    tpl = TEMPLATES[template_key]
    return (
        "ВЕРНИ ТОЛЬКО JSON-объект (без markdown-ограждения, без пояснений) с полями "
        "для видео-шаблона: " + tpl["title"] + ".\n\n"
        "ТЕМА РОЛИКА:\n" + theme.strip() + "\n\n"
        "ПОЛЯ:\n" + tpl["spec"] + "\n\n"
        "Текст-обрамление (бейджи) можно по-русски; имена инструментов, промпты и вывод "
        "терминала — как в реальности (технически, обычно по-английски). Коротко и читаемо.\n"
        "Только JSON."
    )


# ── Извлечение и валидация JSON ──────────────────────────────────────
def _extract_json(raw: str) -> dict:
    """Достаёт JSON-объект из ответа Claude: ``` fenced, чистый, или внутри прозы."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(s[i : j + 1])
        except Exception:
            pass
    raise ScreenBrollError(f"Claude вернул не-JSON: {(raw or '')[:200]!r}")


def _validate_props(props: dict, template_key: str) -> dict:
    """Проверяет обязательные поля, приводит типы, проставляет дефолты шаблона."""
    spec = TEMPLATES[template_key]
    if not isinstance(props, dict):
        raise ScreenBrollError("пропсы не являются объектом")
    out = dict(props)
    for field in spec["required"]:
        v = out.get(field)
        ok = (isinstance(v, str) and v.strip()) or (isinstance(v, list) and v)
        if not ok:
            raise ScreenBrollError(f"нет обязательного поля '{field}' в пропсах")

    if template_key == "AiToolDeepDive":
        ol = out.get("outputLines")
        out["outputLines"] = [str(x) for x in ol][:4] if isinstance(ol, list) else []
        if out.get("toolNameStyle") not in _TOOL_STYLES:
            out["toolNameStyle"] = "block"
        out.setdefault("promptPrefix", ">")
        out.setdefault("tagBadge", "★ РАЗБОР")
        out.setdefault("outroLine", "")
    return out


# ── Claude (JSON, без правки файлов) ─────────────────────────────────
def _claude_env() -> dict:
    """Окружение для Claude CLI. Зеркало auth-логики auto_broll._run_claude:
    CLI отдаёт приоритет ANTHROPIC_API_KEY, поэтому при наличии OAuth-токена
    подписки убираем ключ — иначе подписка не подхватится и пойдёт метеред."""
    env = dict(os.environ)
    oauth = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if oauth:
        env.pop("ANTHROPIC_API_KEY", None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth
    else:
        env["ANTHROPIC_API_KEY"] = _anthropic_key()
    env.setdefault("HOME", str(Path(BROLL_PROJECT).parent))
    return env


def _run_claude_json(prompt: str) -> tuple[dict, float]:
    """Запускает Claude (без allowedTools — файлы не правит), парсит JSON-пропсы.
    → (props_dict, total_cost_usd). Сериализуется по общему flock подписки."""
    from claude_gen_lock import acquire_gen_flock, release_gen_flock, ClaudeGenBusy

    try:
        _flock = acquire_gen_flock("screen_broll")
    except ClaudeGenBusy as e:
        raise ScreenBrollError(str(e))
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            cwd=BROLL_PROJECT, env=_claude_env(),
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
        )
    finally:
        release_gen_flock(_flock)

    if proc.returncode != 0:
        raise ScreenBrollError(f"Claude упал (rc={proc.returncode}): {proc.stderr[:400]}")
    cost, result_text = 0.0, proc.stdout
    try:
        data = json.loads(proc.stdout)
        cost = float(data.get("total_cost_usd", 0.0))
        result_text = data.get("result") or ""
    except Exception as e:
        logger.warning(f"[screen_broll] не распарсил обёртку Claude: {e}")
    return _extract_json(result_text), cost


# ── Рендер ───────────────────────────────────────────────────────────
def _render_screen(comp_id: str, props_path: Path, out_path: Path) -> tuple[bool, str]:
    """Рендерит готовую композицию с кастомными пропсами. → (успех, ошибка)."""
    cmd = ["npx", "remotion", "render", comp_id, str(out_path), f"--props={props_path}"]
    if os.name == "posix":  # nice есть только на POSIX (сервер); локально на Windows — нет
        cmd = ["nice", "-n", "15", *cmd]
    try:
        proc = subprocess.run(
            cmd, cwd=BROLL_PROJECT,
            capture_output=True, text=True, timeout=RENDER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"{comp_id}: таймаут рендера"
    if proc.returncode != 0 or not out_path.exists():
        tail = (proc.stderr or proc.stdout)[-700:]
        return False, f"{comp_id}: {tail}"
    return True, ""


# ── Главный оркестратор ──────────────────────────────────────────────
def generate_screen_broll(
    theme: str, out_dir: str | Path, *,
    template: str = "AiToolDeepDive", comp_id: str | None = None,
) -> tuple[Path, float]:
    """Тема → один готовый экран-ролик mp4 в out_dir/screens/.

    Возвращает (путь_к_клипу, total_cost_usd). Бросает ScreenBrollError.
    """
    if template not in TEMPLATES:
        raise ScreenBrollError(f"неизвестный шаблон: {template}")
    out_dir = Path(out_dir).resolve()
    screens = out_dir / "screens"
    screens.mkdir(parents=True, exist_ok=True)
    if not BROLL_PROJECT.exists():
        raise ScreenBrollError(f"Нет проекта Remotion: {BROLL_PROJECT}")

    comp = comp_id or TEMPLATES[template]["comp_id"]
    # comp_id идёт и в путь файла (props.json/.mp4), и первым позиционным в
    # `remotion render` — формат-гейт против path-traversal и подмены на опцию.
    if comp.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_-]+", comp):
        raise ScreenBrollError(f"недопустимый comp_id: {comp!r}")
    props, cost = _run_claude_json(_build_props_prompt(theme, template))
    props = _validate_props(props, template)

    props_path = screens / f"{comp}.props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    out_path = screens / f"{comp}.mp4"
    out_path.unlink(missing_ok=True)  # не отдать устаревший клип, если рендер не перезапишет

    ok, err = _render_screen(comp, props_path, out_path)
    if not ok:
        raise ScreenBrollError(f"рендер {comp} не удался: {err[:300]}")

    logger.info(f"[screen_broll] ✅ {out_path} (${cost:.4f})")
    return out_path, cost


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _out = sys.argv[1] if len(sys.argv) > 1 else "_screens_test"
    _theme = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read().strip()
    if not _theme:
        print("FAIL: пустая тема")
        sys.exit(1)
    try:
        clip, cost_usd = generate_screen_broll(_theme, _out)
        print(f"OK: {clip} ({clip.stat().st_size // 1024} KB), Claude cost: ${cost_usd:.4f}")
    except ScreenBrollError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
