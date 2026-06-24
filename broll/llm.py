"""LLM-генератор закадрового сценария для B-roll монтажа (Pipeline #2).

Один вызов Claude (Sonnet): тема/тезис → готовый текст закадрового голоса
для вертикального ролика без говорящей головы. Текст идёт напрямую в
generate_voiceover (озвучка голосом бренда). Промпт per-brand —
см. BRAND_VOICE_PROFILES (ядро без бренд-литералов).

Отличие от аватарного сценария: это ЗАКАДР — не «привет, я …», а
повествование/мысль, которая ложится поверх видеоряда.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("broll.llm")

# Нейтральный шаблон: бренд/персона/ниша/CTA приходят ТОЛЬКО из профиля
# (per-brand). Ядро не содержит бренд-литералов — закон core/style
# (docs/PARALLEL_SESSIONS_WORKTREE.md). {brand}/{persona}/{body_hint}/{cta_hint}
# подставляются из BRAND_VOICE_PROFILES.
_SYSTEM_TEMPLATE = """Ты пишешь ЗАКАДРОВЫЙ сценарий для вертикального ролика \
(Instagram Reels / TikTok / Shorts) {brand}. Ролик БЕЗ говорящей головы — твой \
текст звучит закадровым голосом поверх видеоряда из нарезок.

{persona}

═══════════════════════════════════════════════════════════
🚫 ANTI-HALLUCINATION — ГЛАВНОЕ ПРАВИЛО
═══════════════════════════════════════════════════════════
Ты НЕ знаешь личную биографию автора, его конкретные цифры и истории.
ЗАПРЕЩЕНО выдумывать: суммы, даты, эпизоды («однажды…»), имена сотрудников/
партнёров, статистику.
Если дана только тема без конкретики — пиши ОБОБЩЁННО: универсальные мысли, \
верные для любого в этой нише. Не пиши «я» / «мой опыт» — пиши как наблюдение: \
«опыт показывает», «бывает так».

═══════════════════════════════════════════════════════════
СТРУКТУРА (устная речь, ~30-40 секунд звучания)
═══════════════════════════════════════════════════════════
1. ХУК (1 фраза) — сразу цепляет: контраст, вопрос, контринтуитив. Без \
   «привет», «сегодня поговорим», «многие думают».
2. ТЕЛО — одна мысль, развёрнутая на конкретике.{body_hint}
3. ФИНАЛ — короткий вывод или мягкий CTA{cta_hint}. Без агрессивной продажи.

ТРЕБОВАНИЯ:
- 70-110 слов (это ~30-40 сек озвучки).
- Короткие фразы — текст читается вслух. Длинные причастные обороты убивают ритм.
- Грамотный русский. Тире (—), не дефис.
- Без эмодзи, без хэштегов, без списков, без заголовков, без markdown.

ФОРМАТ ОТВЕТА: верни ТОЛЬКО текст сценария — одним-двумя абзацами, \
без пояснений и без кавычек."""


# Профили голоса сценария по бренду. Ключ = brand_name (_get_active_brand_name):
# maksim → Life Drive; default → panferov/Артём. НЕ tenant_id (panferov — тенант,
# default/shoes — бренды). Новый бренд → добавить запись (resolve бросит на unknown).
BRAND_VOICE_PROFILES = {
    "maksim": {
        "brand": "бренда Life Drive (картинг, глэмпинг, SUP, Тюмень)",
        "persona": "Бренд ведёт Максим Юмсунов — предприниматель (16 лет в "
                   "бизнесе). Тон: уверенный, racing-feel, окопный юмор "
                   "предпринимателя, без воды, без инфоцыган.",
        "body_hint": " Картинг/глэмпинг/SUP — как фон и аналогия.",
        "cta_hint": " (заехать на трассу / в глэмпинг / попробовать)",
    },
    "default": {  # panferov / Артём Панфёров
        "brand": "об ИИ, автоматизации и контенте для предпринимателей",
        "persona": "Автор — Артём Панфёров, основатель AI-студии. Пишет о том, "
                   "как ИИ экономит время предпринимателям и практикам. Тон: "
                   "спокойный экспертный, по делу, без воды и инфоцыганщины.",
        "body_hint": " Примеры из ИИ/автоматизации/контента — как иллюстрация мысли.",
        "cta_hint": "",
    },
}


def resolve_voice_profile(brand_name: str) -> dict:
    """Профиль голоса сценария по бренду. Бросает ValueError на неизвестном
    бренде — НЕ выбираем чужую персону молча (CTO-ревью High 3)."""
    try:
        return BRAND_VOICE_PROFILES[brand_name]
    except KeyError:
        raise ValueError(
            f"broll: нет voice-профиля сценария для бренда {brand_name!r}; "
            f"добавь в BRAND_VOICE_PROFILES")


def _build_system(profile: dict) -> str:
    """Собрать system-промпт из нейтрального шаблона + полей профиля."""
    return _SYSTEM_TEMPLATE.format(
        brand=profile["brand"],
        persona=profile["persona"],
        body_hint=profile.get("body_hint", ""),
        cta_hint=profile.get("cta_hint", ""),
    )


def generate_script(
    claude,
    theme: str,
    *,
    brand_name: str = "default",
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 700,
) -> str:
    """Сгенерировать закадровый сценарий по теме/тезису.

    claude     — anthropic.Anthropic клиент.
    theme      — тема или тезис ролика.
    brand_name — активный бренд (_get_active_brand_name); резолвит voice-профиль.
                 Хендлер передаёт снапшотом (llm.py не импортирует bot).

    Возвращает чистый текст сценария. Бросает ValueError при сбое LLM,
    пустом ответе или неизвестном бренде.
    """
    system = _build_system(resolve_voice_profile(brand_name))
    user_msg = (
        f"Тема ролика: {theme}\n\n"
        f"Напиши закадровый сценарий по структуре хук → тело → финал. "
        f"70-110 слов. Верни только текст."
    )
    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text if resp.content else ""
    except Exception as e:
        raise ValueError(f"Anthropic call failed: {type(e).__name__}: {e}") from e

    script = (raw or "").strip().strip('"').strip()
    if len(script) < 40:
        raise ValueError(f"сценарий слишком короткий ({len(script)} симв.)")
    logger.info(f"[broll.llm] сценарий сгенерирован ({len(script)} симв.)")
    return script


__all__ = ["generate_script"]
