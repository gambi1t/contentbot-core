"""
Pipeline #0-B — Idea generator (brand-aware).

Генерирует партии идей контента «в стиле бренда» через Claude Opus.
Используется в Telegram-боте maksim-bot для кнопки `🔍 Идеи дня`.

Поток:
    1. Юзер тапает кнопку → bot.py вызывает `generate_ideas(claude, brand, exclude_titles)`
    2. Функция читает system prompt из `idea_prompt_<brand>.txt`, формирует user-message
       с exclude-листом, дёргает Claude Opus с JSON-prefill
    3. Парсит JSON-массив, прогоняет через Jaccard-фильтр против exclude-листа,
       возвращает финальный список идей (dict'ы)
    4. bot.py рендерит идеи списком + клавиатурой и сохраняет в pending[uid]["ideas_batch"]

JSON-схема одной идеи описана в `idea_prompt_maksim.txt` секции «ВЫХОД».

История:
- Создан 12 May 2026 как часть Pipeline #0-B (Артём, MVP).
- Pipeline #0-A (research трендов из внешних источников) — следующий этап.
"""

from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path

logger = logging.getLogger(__name__)


# ── System-prompt loader ──────────────────────────────────────────────

_IDEA_PROMPT_FILES: dict[str, str] = {
    "maksim": "idea_prompt_maksim.txt",
    # default / другие бренды добавятся по мере расширения
}

# Идея-зацепки — список тем для random injection в user prompt.
# Разрывает детерминизм базового контекста Максима, даёт «свежий ветер»
# в каждой партии — без него Opus крутит одни и те же 5-6 концепций.
# Файл редактируется руками (`idea_seeds_<brand>.txt`).
_IDEA_SEED_FILES: dict[str, str] = {
    "maksim": "idea_seeds_maksim.txt",
}

_IDEA_PROMPT_CACHE: dict[str, str] = {}
_IDEA_SEED_CACHE: dict[str, list[str]] = {}


def _load_idea_prompt(brand: str) -> str | None:
    """Read the idea system prompt for a brand. Cached per-process.

    Returns None if no prompt is configured for the brand (caller should
    surface this as «бренд пока не поддерживает Идеи дня»).
    """
    if brand in _IDEA_PROMPT_CACHE:
        return _IDEA_PROMPT_CACHE[brand]
    filename = _IDEA_PROMPT_FILES.get(brand)
    if not filename:
        return None
    path = Path(__file__).parent / filename
    if not path.exists():
        logger.warning(f"[ideas] Prompt file missing for brand={brand}: {path}")
        return None
    text = path.read_text(encoding="utf-8")
    _IDEA_PROMPT_CACHE[brand] = text
    return text


def _load_idea_seeds(brand: str) -> list[str]:
    """Read the seed list for a brand. Returns [] if no file / no brand.

    Format: one seed per line; lines starting with `#` are comments;
    lines starting with `## SECTION` are section headers (also ignored);
    blank lines ignored.

    Cached per-process; call `reload_idea_prompts()` to drop cache.
    """
    if brand in _IDEA_SEED_CACHE:
        return _IDEA_SEED_CACHE[brand]
    filename = _IDEA_SEED_FILES.get(brand)
    if not filename:
        _IDEA_SEED_CACHE[brand] = []
        return []
    path = Path(__file__).parent / filename
    if not path.exists():
        logger.warning(f"[ideas] Seed file missing for brand={brand}: {path}")
        _IDEA_SEED_CACHE[brand] = []
        return []
    seeds: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        seeds.append(s)
    _IDEA_SEED_CACHE[brand] = seeds
    logger.info(f"[ideas] Loaded {len(seeds)} seed angles for brand={brand}")
    return seeds


def reload_idea_prompts() -> None:
    """Drop the prompt + seed caches — next call hits disk. Useful after
    editing the .txt files without restarting the bot."""
    _IDEA_PROMPT_CACHE.clear()
    _IDEA_SEED_CACHE.clear()


# ── Anti-repetition (text-match, no embeddings — see Plan #3) ────────

_TOKEN_RE = re.compile(r"[^\wа-яё\s]", re.IGNORECASE)


def _tokenize_for_jaccard(s: str) -> set[str]:
    """Lowercase, strip punctuation, split on whitespace, drop short tokens.

    Short tokens (< 3 chars) carry little signal in Russian; dropping them
    avoids false positives on common prepositions (на, в, и, у).
    """
    s = _TOKEN_RE.sub(" ", s.lower())
    return {w for w in s.split() if len(w) >= 3}


def _jaccard(a: str, b: str) -> float:
    ta = _tokenize_for_jaccard(a)
    tb = _tokenize_for_jaccard(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _filter_duplicates(
    ideas: list[dict],
    exclude_titles: list[str],
    threshold: float = 0.45,
) -> tuple[list[dict], list[dict]]:
    """Split ideas into (kept, dropped) based on Jaccard similarity.

    13 May 2026 update — was comparing only `idea.title` against
    `exclude_titles`. That missed «same angle, different wording»
    (e.g. «Стратегия не делается днём» vs «Утром решаю стратегию,
    а днём — никогда» — different titles, same thought).

    Now compares a richer signature `title + first 100 chars of thesis`
    against each exclude entry (which itself may be title-only OR
    title+thesis if caller built it that way — see bot.py
    `ideas_session` builder). Threshold lowered to 0.45 — catches
    semantic paraphrases without too many false positives on legitimate
    different angles.

    Threshold 0.55 (old) was «Большинство строят глэмпинг
    у воды» vs «Почему мы построили глэмпинг в лесу, а не у воды» ≈ 0.45
    vs «Строят глэмпинг у воды и зря» ≈ 0.78 (paraphrase — dropped).
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    if not exclude_titles:
        return list(ideas), []
    for idea in ideas:
        title = (idea.get("title") or "").strip()
        if not title:
            dropped.append(idea)
            continue
        # Build richer signature for the new idea — title + thesis prefix
        thesis = (idea.get("central_thesis") or "").strip()[:100]
        signature = f"{title} {thesis}".strip()
        max_sim = max((_jaccard(signature, ex) for ex in exclude_titles), default=0.0)
        if max_sim >= threshold:
            dropped.append(idea)
        else:
            kept.append(idea)
    return kept, dropped


# ── JSON parsing (tolerant to ```json wrappers) ──────────────────────

def _parse_ideas_json(raw: str) -> list[dict]:
    """Extract a JSON array from Claude's response. Strips markdown
    code fences if present, locates the first '[' and last ']'.

    Raises ValueError if no array is found or parsing fails.
    """
    s = raw.strip()
    # Strip ```json / ``` wrappers if Claude ignored the «no markdown» rule
    s = re.sub(r"^```(?:json)?\s*\n?", "", s)
    s = re.sub(r"\n?\s*```$", "", s)
    # Locate the array boundaries — defensive, in case Claude prefixed
    # the response with «Вот идеи:\n[...]»
    i = s.find("[")
    j = s.rfind("]")
    if i < 0 or j < 0 or j <= i:
        raise ValueError(f"No JSON array in response (first 200 chars): {raw[:200]!r}")
    try:
        arr = json.loads(s[i:j + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON decode failed: {e} (extracted: {s[i:j + 1][:300]!r})")
    if not isinstance(arr, list):
        raise ValueError(f"Expected list, got {type(arr).__name__}")
    return arr


def _validate_idea(idea: dict) -> bool:
    """Quick sanity check: required string fields present and non-empty.

    Doesn't enforce enum values (niche / format_type) — that's left to
    downstream rendering; we just want to drop garbage entries.
    """
    required = ("title", "hook_draft", "central_thesis")
    for field in required:
        v = idea.get(field)
        if not isinstance(v, str) or not v.strip():
            return False
    return True


# ── Main API ──────────────────────────────────────────────────────────

def generate_ideas(
    claude,  # anthropic.Anthropic instance (typed loosely to avoid import dep)
    brand: str,
    exclude_titles: list[str] | None = None,
    n: int = 10,
    model: str = "claude-opus-4-7",
    max_tokens: int = 4096,
) -> list[dict]:
    """Generate `n` content ideas for `brand`.

    Args:
        claude: Anthropic client (already constructed in bot.py)
        brand: brand key (must exist in _IDEA_PROMPT_FILES)
        exclude_titles: list of existing card titles to NOT repeat —
            capped to 80 most-recent before sending to LLM
        n: target idea count (LLM may return slightly fewer after
            duplicate filtering)
        model: Anthropic model id. Default `claude-opus-4-7` — same as
            tg_post_writer's draft pass (Opus is required for the nuance
            on Maksim's voice).
        max_tokens: max output tokens. 4096 covers ~10 ideas with
            full schema; raise if n grows past 12.

    Returns:
        List of validated idea dicts. May be shorter than `n` if dupes
        were filtered out.

    Raises:
        ValueError: brand has no prompt configured, or LLM returned
        invalid JSON.
    """
    system_prompt = _load_idea_prompt(brand)
    if not system_prompt:
        raise ValueError(f"No idea prompt for brand: {brand}")

    exclude_titles = exclude_titles or []
    capped_exclude = exclude_titles[:80]  # see Plan: ~5K tokens budget
    exclude_block = ""
    if capped_exclude:
        exclude_block = (
            "\n\nУЖЕ БЫЛО (НЕ повторяй и НЕ перефразируй эти темы — "
            "если близкая тема, возьми другой угол или другую нишу):\n"
            + "\n".join(f"- {t}" for t in capped_exclude)
        )

    # 13 May 2026 — seed injection. Without this, Opus crutches on the
    # ~5-6 base concepts it can infer from Maksim's fixed context
    # (delegation/control, strategy vs ops, family vs business) and
    # serves the same ideas in different wording. Seeds break that —
    # random 7 angles from a curated pool (~180 lines across 8
    # directions) get injected as «inspiration, not mandate».
    seeds_block = ""
    seed_pool = _load_idea_seeds(brand)
    if seed_pool:
        seeds_n = min(7, len(seed_pool))
        seeds_sample = random.sample(seed_pool, seeds_n)
        seeds_block = (
            "\n\nЗАЦЕПКИ (можно использовать как стартовые углы или "
            "придумать свои — это вдохновение, не обязательство; смешай "
            "с другими углами в партии):\n"
            + "\n".join(f"- {s}" for s in seeds_sample)
        )

    user_msg = (
        f"Сгенерируй {n} идей контента для Максима.{exclude_block}{seeds_block}\n\n"
        f"🚨 ФОРМАТ ОТВЕТА — критично:\n"
        f"1. Твой ответ ДОЛЖЕН начинаться ровно с символа [ (квадратная скобка)\n"
        f"2. И заканчиваться ровно символом ]\n"
        f"3. БЕЗ markdown-блоков (никаких ```json или ```)\n"
        f"4. БЕЗ вступительных фраз («Вот идеи:», «Конечно,» и т.п.)\n"
        f"5. БЕЗ комментариев после массива\n"
        f"6. ТОЛЬКО валидный JSON-массив длины {n}"
    )

    # Note (12 May 2026): we tried assistant-message prefill `[` here to
    # nudge JSON output, but Opus 4.7 rejects it with
    # `invalid_request_error: This model does not support assistant
    # message prefill`. Instead we rely on strict format instructions
    # in user_msg + tolerant parser (_parse_ideas_json strips markdown
    # wrappers and locates [...] anywhere in the response).
    resp = claude.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_msg},
        ],
    )

    raw = resp.content[0].text if resp.content else ""
    logger.info(f"[ideas] Generated raw response ({len(raw)} chars) for brand={brand}")

    try:
        parsed = _parse_ideas_json(raw)
    except ValueError as e:
        logger.error(f"[ideas] JSON parse failed: {e}")
        raise

    # Drop garbage entries
    validated = [i for i in parsed if _validate_idea(i)]
    if len(validated) < len(parsed):
        logger.warning(
            f"[ideas] Dropped {len(parsed) - len(validated)} invalid entries"
        )

    # Anti-repetition post-filter
    kept, dropped = _filter_duplicates(validated, capped_exclude)
    if dropped:
        logger.info(
            f"[ideas] Dropped {len(dropped)} near-duplicates "
            f"(threshold=0.55): {[d.get('title', '?')[:40] for d in dropped]}"
        )

    return kept


# ── Rendering helpers (used by bot.py to format the chat message) ────

# Map of subtype → human-friendly label for the message body
_FORMAT_LABELS = {
    "hook_tour": "🎬 видео-тур + hook",
    "hook_teaser": "🎬 hook-тизер + лонгрид",
    "talking_head_outdoor": "🎙 talking head на природе",
    "skit_humor": "😂 скетч",
    "review_essay": "📝 длинный пост с моралью",
    "review_list": "📋 тезисный пост",
    "thesis": "💭 короткий пост-тезис",
}

_NICHE_LABELS = {
    "glamping": "🌲 глэмпинг",
    "karting": "🏎 картинг",
    "entrepreneur_general": "💼 предпринимательство",
}

_AUDIENCE_LABELS = {
    "entrepreneurs": "предприниматели",
    "clients": "клиенты",
    "smm": "SMM",
}


def format_idea_line(idx: int, idea: dict) -> str:
    """Render a single idea as a COMPACT block for chat display.

    `idx` is 1-based for human display.

    12 May 2026 — switched to compact format: only title + one-liner
    thesis fit in chat. Full hook/thesis/why_works/audience are stored
    in the Notion card body upon save (see bot.py `idea_save:` handler).
    Reason: Telegram message hard limit is 4096 chars, and 10 ideas with
    verbose format exceeded it (caught by «Message_too_long» BadRequest
    in production).
    """
    title = idea.get("title", "?")
    thesis = idea.get("central_thesis", "")
    niche = _NICHE_LABELS.get(idea.get("niche", ""), "")
    subtype = _FORMAT_LABELS.get(idea.get("format_subtype", ""), "")
    # Trim thesis to one chat-friendly line; full text is in the
    # Notion card body. ~140 chars is the visual sweet spot.
    if len(thesis) > 140:
        thesis = thesis[:137].rstrip() + "…"
    lines = [
        f"<b>{idx}. {title}</b>",
        f"<i>{niche} · {subtype}</i>",
    ]
    if thesis:
        lines.append(thesis)
    return "\n".join(lines)


def format_ideas_message(ideas: list[dict]) -> str:
    """Render the full batch as a single chat message (HTML mode).

    Layout deliberately compact — see `format_idea_line` docstring.
    """
    if not ideas:
        return "⚠️ Не получилось сгенерировать идеи. Попробуй ещё раз."
    blocks = [format_idea_line(i + 1, idea) for i, idea in enumerate(ideas)]
    header = (
        f"🎰 <b>Банк идей — {len(ideas)} вариантов</b>\n"
        f"<i>Полные сценарии (hook/тезис/почему работает) сохранятся в Notion.</i>\n\n"
    )
    return header + "\n\n".join(blocks)


__all__ = [
    "generate_ideas",
    "format_idea_line",
    "format_ideas_message",
    "reload_idea_prompts",
]
