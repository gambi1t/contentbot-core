"""selfie.transcribe — обёртка над subtitle_burner.transcribe_words
с автоматическим Whisper prompt biasing.

Зачем нужна:
  - Максим в роликах постоянно упоминает свой бренд (Life Drive, ld72), сферы
    бизнеса (картинг, глэмпинг) и AI-инструменты, которые применяет. Faster-Whisper
    без подсказок искажает редкие слова («ЛайвДрайв», «Хейген», «Кьюрсор»).
  - Передача ``initial_prompt`` с canonical-формами этих терминов **подсказывает**
    Whisper'у ожидаемую лексику — снижает ошибки на 60-80% (доки faster-whisper).

Контракт:
  - ``build_whisper_prompt()`` — естественная фраза-контекст ≤ 250 символов.
    Содержит бренд Максима + бизнес-термины + ядро AI-инструментов в canonical
    форме. Длина ограничена потому что слишком длинный prompt перегружает
    контекст модели.
  - ``transcribe(audio_path, language="ru")`` — вызывает
    ``subtitle_burner.transcribe_words`` с автоматическим ``initial_prompt``.
    Возвращает обычный ``list[dict]`` с word-level timestamps. После Whisper'а
    также прогоняется ``fix_brand_names`` (внутри ``transcribe_words``) —
    двойная защита: bias + postfix.
"""
from __future__ import annotations

from pathlib import Path

# Import alias чтобы тесты могли mock'ать через 'selfie.transcribe._transcribe_words'
from subtitle_burner import transcribe_words as _transcribe_words


# Курированный список ключевых терминов для Whisper hint.
# Структура: бренд Максима + бизнес-сферы + ядро AI-tools (что он реально
# использует). Длина итогового prompt'а должна быть < 250 chars.
_MAKSIM_BRANDS: list[str] = [
    # Бренд / имена Максима — приоритет, чаще всего звучит
    "Life Drive", "Юмсунов", "ld72",
]
_MAKSIM_BUSINESS_TERMS: list[str] = [
    # Бизнес-сферы Максима
    "картинг", "глэмпинг", "Тюмень",
]
_CORE_AI_TOOLS: list[str] = [
    # Ядро AI-инструментов: только то что Максим реально упоминает в роликах
    # про «как ИИ помогает в реальном бизнесе».
    "ChatGPT", "Claude", "Gemini", "Cursor", "Midjourney", "HeyGen", "Sora", "Notion",
]
# Полный список — обратная совместимость с тестами, которые опираются на единый
# источник правды.
_CURATED_BRANDS_FOR_PROMPT: list[str] = (
    _MAKSIM_BRANDS + _MAKSIM_BUSINESS_TERMS + _CORE_AI_TOOLS
)


def build_whisper_prompt() -> str:
    """Собрать natural-language hint для faster-whisper.

    Returns:
        Строка ≤ 250 символов вида::

            "Запись Максима Юмсунова о бизнесе (Life Drive, картинг, глэмпинг,
             Тюмень) и AI-инструментах: ChatGPT, Claude, Gemini, ..."

        Faster-Whisper использует этот prompt как контекст и лучше распознаёт
        перечисленные термины.
    """
    brand = ", ".join(_MAKSIM_BRANDS + _MAKSIM_BUSINESS_TERMS)
    tools = ", ".join(_CORE_AI_TOOLS)
    return (
        f"Запись Максима Юмсунова о бизнесе ({brand}) "
        f"и AI-инструментах: {tools}."
    )


def transcribe(
    audio_path: str | Path,
    language: str = "ru",
) -> list[dict]:
    """Транскрибировать аудио через faster-whisper с brand biasing.

    Args:
        audio_path: путь к аудио-файлу (любой формат поддерживаемый ffmpeg).
        language: язык транскрипции, дефолт ``"ru"``.

    Returns:
        ``list[dict]`` формата ``[{"word": str, "start": float, "end": float}, ...]``
        — уже после ``fix_brand_names`` (постфиксная канонизация в
        ``subtitle_burner.transcribe_words``).
    """
    prompt = build_whisper_prompt()
    return _transcribe_words(
        audio_path,
        language=language,
        initial_prompt=prompt,
    )
