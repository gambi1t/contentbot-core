"""Style contract — единый источник дизайн-правил для всех HF-сцен.

По ревью ChatGPT 4 июня 2026 (Phase 1 Step 2 production-плана): заменяет
`done_scenes` cross-talk на статичный контракт, который inline-ится в каждый
subagent-промпт одинаковый. Так единство стиля держится при параллельной
генерации (где cross-talk сцен невозможен по архитектуре).

Контракт лежит в `hyperframes_assets/style_contract.json` (в репо, deploy
выкладывает рядом с reference_pack.md). Если файла нет — модуль падает
явно (не silently дефолтит).

Публичное API:
  load_style_contract(path=None) -> dict
  inline_for_prompt(contract) -> str          (markdown-секция для _build_scene_prompt)
  check_forbidden_in_html(html, contract) -> list[str]   (нарушения в HTML)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_DEFAULT_PATH = (
    Path(__file__).resolve().parent / "hyperframes_assets" / "style_contract.json"
)


def _active_contract_path() -> Path:
    """Путь к style-контракту активного тенанта (per-tenant, HF срез C).

    Generic: `style_contract.<tenant_id>.json` если есть, иначе дефолт
    (`style_contract.json`). `tenant_id` — из `tenant.active_tenant_id()`
    (lazy import, чтобы style_contract оставался импортируемым standalone).
    panferov → style_contract.panferov.json (Nox Dark); maksim/default → дефолт.
    """
    try:
        import tenant
        tid = tenant.active_tenant_id()
    except Exception:
        return _DEFAULT_PATH
    cand = _DEFAULT_PATH.parent / f"style_contract.{tid}.json"
    return cand if cand.exists() else _DEFAULT_PATH


# ── загрузка ─────────────────────────────────────────────────────────────
def load_style_contract(path: Path | str | None = None) -> dict[str, Any]:
    """Читает JSON-контракт. Без `path` — контракт активного тенанта
    (`_active_contract_path()`: panferov → style_contract.panferov.json,
    иначе style_contract.json).

    Не используем кэширование — файл маленький (~1KB), читается раз на старт
    оркестратора, при тестах удобно перечитывать.
    """
    p = Path(path) if path else _active_contract_path()
    if not p.exists():
        raise FileNotFoundError(
            f"style_contract.json не найден по пути {p}. Это критичный артефакт "
            f"production-pipeline (см. docs/hyperframes_production_plan_review_"
            f"2026-06-04.md Step 2). Проверь deploy."
        )
    return json.loads(p.read_text(encoding="utf-8"))


# ── inline в промпт ──────────────────────────────────────────────────────
def inline_for_prompt(contract: dict[str, Any]) -> str:
    """Сворачивает контракт в текстовый блок для встройки в subagent-промпт.

    Формат компактный, ~600-800 символов — заменяет блок ПРАВИЛА в текущем
    `_build_scene_prompt`, при этом структурируется явно (палитра / типографика /
    safe-area / запреты), а не «свободный текст».
    """
    p = contract["palette"]
    t = contract["typography"]
    s = contract["spacing"]
    f = contract["frame"]
    sa = s["safe_area"]

    forbidden = ", ".join(f'«{x}»' for x in contract["forbidden_labels"])

    block = f"""STYLE CONTRACT (фиксированный, един для всех сцен):

КАДР: {f['width']}×{f['height']}px, длительность {f['duration_s']}с.
data-composition-id="<scene_id>", data-width="{f['width']}", data-height="{f['height']}", data-duration="{f['duration_s']}".

ПАЛИТРА (НЕ выдумывай других цветов):
- фон: {p['bg_primary']} (или {p['bg_secondary']} для подложек)
- акцент: {p['accent']} (для ключевого слова, дот, кружков-маркеров)
- текст: {p['text_primary']} (основной), {p['text_muted']} (подписи)

ТИПОГРАФИКА:
- HERO заголовок: {t['primary_family']} 800, {t['hero_min_px']}-{t['hero_max_px']}px, line-height 0.92-1.12, letter-spacing -0.04em.
- body: {t['body_family']} 500, {t['body_min_px']}-{t['body_max_px']}px.
- kicker (надзаголовок): {t['body_family']} 600, {t['kicker_min_px']}-{t['kicker_max_px']}px, letter-spacing 0.04-0.32em, uppercase.
- caption/sub: {t['body_family']} 500, {t['caption_min_px']}-{t['caption_max_px']}px.
- @font-face: 6 woff2 (cyrillic+latin для Inter Tight 800, Inter 500, Inter 600) — копируй из index.html дословно.

SAFE-AREA: ВЕСЬ смысловой контент в x∈[{sa['x'][0]},{sa['x'][1]}], y∈[{sa['y'][0]},{sa['y'][1]}].
Контейнер — flex-column (display:flex; flex-direction:column; gap:{s['gap_min_px']}px; padding:{s['padding_px']}px), БЕЗ position:absolute;top на текстах.
Absolute разрешён только для декора/SVG/glow/ghost-text.

ДЕТЕРМИНИЗМ: НЕ Math.random, НЕ Date.now, НЕ repeat:-1, НЕ exit-анимаций (кроме scene_06).
Регистрируй `window.__timelines["<scene_id>"] = gsap.timeline({{paused:true}})`.

ВХОД ТЕКСТА: только opacity / translate / scale. 🔴 filter:blur НА ТЕКСТЕ
ЗАПРЕЩЁН — текст обязан быть РЕЗКИМ с первого кадра появления (зритель видит
«мыло» вместо слова). blur разрешён ТОЛЬКО декору: glow-пятна, ghost-text фона.

ВНЕШНИЕ URL: разрешены ТОЛЬКО — cdn.jsdelivr.net (GSAP), fonts.googleapis.com,
fonts.gstatic.com, unpkg.com, w3.org (SVG xmlns). Любой другой URL — рендер оффлайн.

🔴 ЗАПРЕЩЕНЫ В ФИНАЛЬНОМ HTML (forbidden labels, для клиента — не для разработчика):
{forbidden}.
Это служебные метки, в финальном видео их быть не должно.
"""
    return block


# ── валидация HTML ───────────────────────────────────────────────────────
def check_forbidden_in_html(html: str, contract: dict[str, Any]) -> list[str]:
    """Сканирует HTML на forbidden_labels + forbidden_patterns_regex.

    Игнорирует:
    - HTML-комментарии `<!-- ... -->` (можно пометить разработчику что почему,
      это не показывается в кадре)
    - JS-комментарии `// ...` и `/* ... */`
    - значения атрибутов `data-composition-id="scene_02"` и id="scene_02" —
      это легитимная разметка, а не зрительская метка

    Возвращает список уникальных найденных строк (для лога/фидбека субагенту).
    """
    # вырезаем комментарии HTML/JS чтобы не словить «debug» в /* todo */
    cleaned = html
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"//[^\n]*", " ", cleaned)
    # вырезаем значения атрибутов вида ="..." и ='...' внутри тегов — это техника,
    # туда легитимно ложится scene_02. Но осторожно: вырезаем ТОЛЬКО внутри
    # открывающих тегов, чтобы не потерять текстовый контент <div>SCENE 06</div>.
    def _strip_attrs(match):
        tag = match.group(0)
        # внутри тега заменяем "..."/'...' пустотой
        return re.sub(r'="[^"]*"|=\'[^\']*\'', "=\"\"", tag)
    cleaned = re.sub(r"<[^>]+>", _strip_attrs, cleaned)

    found: list[str] = []
    # literal labels
    for label in contract.get("forbidden_labels", []):
        if label in cleaned:
            found.append(label)
    # regex patterns (case-insensitive по умолчанию, без \b если в шаблоне)
    for pattern in contract.get("forbidden_patterns_regex", []):
        for m in re.findall(pattern, cleaned, flags=re.IGNORECASE):
            if isinstance(m, tuple):
                m = m[0]
            found.append(m)
    # уникализируем сохраняя порядок
    seen = set()
    uniq = []
    for x in found:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq
