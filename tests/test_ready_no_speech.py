"""TDD: готовый ролик БЕЗ речи (/ready) — спросить контекст вместо отказа.

Кейс Артёма: ролик из CapCut без слов (сравнение Seedance/Kling, 10с). Расшифровка
пустая → раньше код жёстко отбраковывал («отправь другое с чёткой речью»,
selfie/handlers.py:353). Теперь для готового ролика (finished) пустой транскрипт →
состояние awaiting_ready_context: юзер пишет/надиктовывает «о чём ролик + что
подчеркнуть» → текст ложится как транскрипт → название/описание из него.

Реюз: build_finished_pending (готовый ролик = финал), паттерн awaiting_carousel_theme
(текст+голос в одном стейте). НЕ трогаем живое selfie (там пустой транскрипт = отказ
корректно: говорящая голова обязана иметь речь).

Запуск: python tests/test_ready_no_speech.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

for _m in ("telegram", "telegram.ext", "selfie.broll_picker", "selfie.cover",
           "selfie.music", "selfie.edit", "selfie.transcribe"):
    sys.modules.setdefault(_m, MagicMock())

sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie import handlers as sh  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


CTX = "сравнение редакторов Seedance и Kling, упор что Kling реалистичнее в движении"


def test_apply_ready_context_builds_finished(errors):
    print("\n-- apply_ready_context: контекст → finished pending (контекст=транскрипт) --")
    entry = {
        "state": "awaiting_ready_context", "selfie_finished": True,
        "selfie_tmp_dir": "/tmp/x", "selfie_source": "/tmp/x/source.mp4",
    }
    p = sh.apply_ready_context(entry, CTX)
    _assert(p["state"] == "selfie_cover_picking", f"сразу к обложке (state={p['state']!r})", errors)
    _assert(p["selfie_finished"] is True, "флаг готового ролика сохранён", errors)
    _assert(p["selfie_transcript"] == CTX, "контекст лёг как транскрипт (для названия)", errors)
    _assert(p["selfie_orig_transcript"] == CTX, "контекст = orig_transcript", errors)
    _assert(p["selfie_final"] == "/tmp/x/source.mp4", "видео = исходник (не переобрабатываем)", errors)


def test_apply_ready_context_strips(errors):
    print("\n-- apply_ready_context: тримит пробелы контекста --")
    entry = {"selfie_tmp_dir": "/tmp/x", "selfie_source": "/tmp/x/s.mp4"}
    p = sh.apply_ready_context(entry, "  " + CTX + "  ")
    _assert(p["selfie_transcript"] == CTX, "пробелы обрезаны", errors)


def test_source_no_speech_finished_asks_context(errors):
    print("\n-- selfie/handlers.py:353 ветка — finished+пусто → awaiting_ready_context, не отказ --")
    src = Path(sh.__file__).read_text(encoding="utf-8")
    idx = src.find("if not transcript_text.strip():")
    _assert(idx != -1, "точка пустого транскрипта найдена", errors)
    if idx == -1:
        return
    window = src[idx: idx + 900]
    _assert("awaiting_ready_context" in window,
            "для готового ролика ставится awaiting_ready_context (не отказ)", errors)
    _assert("finished" in window, "ветка гейтится флагом finished (живое selfie не трогаем)", errors)


def test_routing_text_and_voice(errors):
    print("\n-- bot.py: awaiting_ready_context ловится текстом (process_idea) и голосом (process_voice) --")
    src = (Path(sh.__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    cnt = src.count('awaiting_ready_context')
    _assert(cnt >= 2, f"состояние обрабатывается в ≥2 местах (текст+голос), got {cnt}", errors)
    _assert("apply_ready_context" in src, "роутер зовёт apply_ready_context", errors)


def main() -> int:
    errors: list = []
    for fn in (test_apply_ready_context_builds_finished, test_apply_ready_context_strips,
               test_source_no_speech_finished_asks_context, test_routing_text_and_voice):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK all ready-no-speech tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
