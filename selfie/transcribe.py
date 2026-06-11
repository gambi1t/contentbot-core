"""selfie.transcribe — обёртка над subtitle_burner.transcribe_words
с автоматическим Whisper prompt biasing.

Зачем нужна:
  - Артём в роликах постоянно упоминает AI-инструменты (ChatGPT, Claude,
    Cursor, ...). Faster-Whisper без подсказок их искажает («Джеминай»,
    «Кьюрсор»). Постфиксный fix через ``subtitle_burner.fix_brand_names``
    спасает только если Whisper выдал известное искажение из словаря.
  - Передача ``initial_prompt`` с canonical-формами этих брендов **подсказывает**
    Whisper'у ожидаемую лексику — снижает ошибки на 60-80% (доки faster-whisper).

Контракт:
  - ``build_whisper_prompt()`` — естественная фраза-контекст, 200-250 символов.
    Содержит топ-22 AI-бренда в canonical English. Длина ограничена потому что
    слишком длинный prompt перегружает контекст модели.
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


# Курированный список топ-брендов для Whisper hint (canonical English).
# НЕ весь словарь _BRAND_CANONICAL (там 80 entries с искажениями) — только
# те бренды которые Артём реально упоминает в роликах и для которых критично
# не допустить ошибки Whisper'а. Длина итогового prompt'а должна быть < 250 chars.
_CURATED_BRANDS_FOR_PROMPT: list[str] = [
    # LLMs
    "ChatGPT", "Claude", "Opus", "Sonnet", "GPT-5", "Gemini", "Grok", "DeepSeek",
    # Code-tools
    "Cursor", "Lovable", "Bolt", "Windsurf", "Cline",
    # Image / Video
    "Midjourney", "Sora", "Veo", "Runway", "Kling", "HeyGen",
    # Companies
    "OpenAI", "Anthropic",
    # Audio
    "Suno",
]


def build_whisper_prompt() -> str:
    """Собрать natural-language hint для faster-whisper из curated brand list.

    Returns:
        Строка длиной 200-250 символов вида::

            "Запись о работе с AI-инструментами: ChatGPT, Claude, Opus,
             Sonnet, GPT-5, ..."

        Faster-Whisper использует этот prompt как контекст и лучше распознаёт
        перечисленные термины.
    """
    brand_list = ", ".join(_CURATED_BRANDS_FOR_PROMPT)
    return f"Запись о работе с AI-инструментами: {brand_list}."


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
