"""TDD P4 (Артём 30.06): на пути Аватар+B-roll (card_asm_go «+субтитры») субтитры
жгутся Whisper-ом с ошибками, поправить негде (в отличие от селфи).

Фикс (реюз селфи-механизма): перед прожигом даём поправить транскрипт.
- `_start_subtitle_review`: расшифровать аватар-аудио → показать транскрипт →
  правка (реюз apply_user_edits, word-count-lock) → words.json.
- Гейт в card_asm_go ПОСЛЕ proj_dir: with_subs && нет words.json → ревью → return.
  После подтверждения words.json есть → повторная сборка по правленым словам (Whisper
  не зовётся). Резюм по кнопке (без рефактора сборщика).
- Callbacks subrev:edit/go/cancel + state subrev_review/subrev_editing.
- subrev-ключ card-scoped (очистка при переключении карточки).

Запуск: python tests/test_subtitle_review_p4.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def test_words_to_transcript(errors):
    print("\n-- _words_to_transcript: слова → строка --")
    words = [{"word": "Промпт", "start": 0, "end": 0.4},
             {"word": "за", "start": 0.4, "end": 0.5},
             {"word": "2990", "start": 0.5, "end": 1.0}]
    _assert(bot._words_to_transcript(words) == "Промпт за 2990", "склейка .word через пробел", errors)
    _assert(bot._words_to_transcript([]) == "", "пусто → ''", errors)
    _assert(bot._words_to_transcript(None) == "", "None → ''", errors)


def test_review_component_wired(errors):
    print("\n-- компонент subrev + гейт в card_asm_go --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    _assert("async def _start_subtitle_review" in src, "_start_subtitle_review определён", errors)
    # гейт в card_asm_go
    idx = src.find('query.data.startswith("card_asm_go:")')
    region = src[idx: idx + 3000] if idx != -1 else ""
    _assert('_start_subtitle_review(' in region, "гейт зовёт _start_subtitle_review в card_asm_go", errors)
    _assert('words.json' in region and 'with_subs' in region, "гейт: with_subs && нет words.json", errors)
    # callbacks
    for cb in ('"subrev:edit"', '"subrev:go"', '"subrev:cancel"'):
        _assert(f'query.data == {cb}' in src, f"callback {cb} обработан", errors)
    # текст-ветка правки
    _assert('"subrev_editing"' in src, "state subrev_editing обрабатывается в process_idea", errors)
    # реюз чистых селфи-функций
    _assert("from selfie.edit import apply_user_edits" in src, "реюз apply_user_edits", errors)
    _assert("build_review_message" in src, "реюз build_review_message", errors)
    _assert("transcribe_words" in src, "реюз transcribe_words", errors)


def test_avatar_publish_wired(errors):
    print("\n-- avatar_publish (аватар+субтитры без broll) тоже под ревью --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    idx = src.find('query.data == "avatar_publish"')
    region = src[idx: idx + 1800] if idx != -1 else ""
    _assert('_start_subtitle_review(' in region and 'resume_cb="avatar_publish"' in region,
            "avatar_publish: гейт ревью с резюмом на себя", errors)
    _assert("words=_apw" in region,
            "avatar_publish: жжёт по words.json (без ре-транскрибации)", errors)


def test_subrev_card_scoped(errors):
    print("\n-- subrev очищается при переключении карточки --")
    _assert("subrev" in bot._CARD_SCOPED_KEYS, "subrev в _CARD_SCOPED_KEYS", errors)


def main() -> int:
    errors: list = []
    for fn in (test_words_to_transcript, test_review_component_wired,
               test_avatar_publish_wired, test_subrev_card_scoped):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK subtitle-review P4 tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
