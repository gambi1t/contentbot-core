"""selfie.edit — позиционная замена слов в транскрипции с сохранением timestamps.

Главная функция: ``apply_user_edits(orig_words, new_text) → (new_words, warning)``.

Контракт:
  - Юзер правит транскрипцию ТОЛЬКО на уровне орфографии слов (например,
    «Джеминай» → «Gemini»). Кол-во слов и порядок не меняются.
  - Функция делает позиционный replace: word[i].word := new_tokens[i], timestamps
    сохраняются 1:1.
  - Если кол-во слов изменилось (юзер удалил/добавил) — возвращаем КОПИЮ оригинала
    + warning-строку. Бот покажет warning юзеру и попросит ограничиться орфографией.
  - Никогда не мутируем входной orig_words — всегда возвращаем новый список.
"""
from __future__ import annotations


def apply_user_edits(
    orig_words: list[dict],
    new_text: str,
) -> tuple[list[dict], str | None]:
    """Заменить .word в orig_words на токены из new_text (позиционно).

    Args:
        orig_words: список ``[{"word": str, "start": float, "end": float}, ...]``
            из ``subtitle_burner.transcribe_words``.
        new_text: исправленный пользователем текст. Разбивается по whitespace —
            extra spaces / leading / trailing пробелы игнорируются.

    Returns:
        Tuple ``(new_words, warning)``:
          - ``new_words`` — новый список (копия orig'а с заменёнными .word).
            При warning — копия orig'а без изменений.
          - ``warning`` — ``None`` при успешной замене; строка-описание расхождения
            если кол-во слов не совпало.

    Examples:
        >>> orig = [{"word": "Джеминай", "start": 0, "end": 0.5},
        ...         {"word": "умеет", "start": 0.6, "end": 1.0}]
        >>> new_words, w = apply_user_edits(orig, "Gemini умеет")
        >>> new_words[0]["word"]
        'Gemini'
        >>> w is None
        True
    """
    # Токенизация new_text: split() без аргументов нормализует whitespace
    # (множественные пробелы / табы / leading-trailing → один разделитель).
    new_tokens = new_text.split()

    n_orig = len(orig_words)
    n_new = len(new_tokens)

    # Edge: оба пустые → возвращаем пустой список, без warning
    if n_orig == 0 and n_new == 0:
        return [], None

    # Mismatch: возвращаем КОПИЮ orig (deep, чтобы caller не мутировал нашу память)
    if n_orig != n_new:
        warning = (
            f"⚠️ Я могу править только орфографию слов, не структуру. "
            f"В оригинале {n_orig} слов(а), в твоём тексте {n_new}. "
            f"Пришли исправленный текст с тем же количеством слов, "
            f"либо запиши видео заново если хочешь переписать речь."
        )
        return [dict(w) for w in orig_words], warning

    # Happy path: позиционная замена .word, остальное (start/end и любые
    # дополнительные поля) сохраняем как есть.
    new_words = []
    for orig_w, new_tok in zip(orig_words, new_tokens):
        replaced = dict(orig_w)  # копия чтобы не мутировать orig
        replaced["word"] = new_tok
        new_words.append(replaced)

    return new_words, None
