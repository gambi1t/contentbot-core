"""B-roll sync P0 — matcher script_beat → selfie_words (hyperframes_alignment).

Фикстура — реальный транскрипт ролика Артёма (23.06). Проверяем: нормализацию,
точный/обрезанный beat, sequential-курсор (повторные слова не откатывают назад),
unmatched при чужих словах, и агрегат align_storyboard_to_words.

Run: python tests/test_hf_alignment.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_alignment as al  # noqa: E402

_TRANSCRIPT = (
    "Всем привет Пропал почти на два месяца Сейчас покажу на что я их потратил "
    "За это время собрал несколько рабочих штук Голосового ассистента которая "
    "ведет дела за меня Контент завод который делает ролики практически сам "
    "Ещё пару клиентов попросили рабочие инструменты которые я тоже реализовал "
    "Подпишись чтобы не пропустить"
)


def _mk_words(text: str, dt: float = 0.4) -> list[dict]:
    toks = text.split()
    return [{"word": w, "start": round(i * dt, 3), "end": round(i * dt + dt * 0.9, 3)}
            for i, w in enumerate(toks)]


_WORDS = _mk_words(_TRANSCRIPT)


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_normalize(errors):
    print("\n-- normalize_ru_text: ё→е, пунктуация, lowercase --")
    _assert(al.normalize_ru_text("Голосового, ассистента!") == ["голосового", "ассистента"],
            "пунктуация снята, lowercase", errors)
    _assert(al.normalize_ru_text("ведёт") == ["ведет"], "ё→е", errors)
    _assert(al.normalize_ru_text("") == [], "пусто → []", errors)


def test_exact_beat(errors):
    print("\n-- точный beat (verbatim фрагмент) → корректное окно + высокая confidence --")
    m = al.align_beat_to_words("Голосового ассистента которая ведет дела за меня", _WORDS)
    _assert(m["status"] == "aligned", f"status aligned (conf={m['confidence']})", errors)
    # проверяем что окно реально на словах «голосового..меня»
    span = " ".join(_WORDS[i]["word"] for i in range(m["word_start"], m["word_end"]))
    # окно = от первого до последнего ЯКОРЯ (стоп-слово «меня» не якорь — это ок,
    # планировщик нормализует длину + post_roll). Ядро фразы покрыто.
    _assert("Голосового" in span and "ассистента" in span and "дела" in span,
            f"окно покрывает ядро фразы: {span!r}", errors)
    _assert(m["time_start"] == _WORDS[m["word_start"]]["start"], "time_start = старт первого слова", errors)


def test_trimmed_beat(errors):
    print("\n-- обрезанный beat (Claude убрал филлеры) → якоря найдены, aligned --")
    m = al.align_beat_to_words("Контент завод делает ролики", _WORDS)
    _assert(m["status"] == "aligned", f"подмножество слов → aligned (conf={m['confidence']})", errors)
    span = " ".join(_WORDS[i]["word"] for i in range(m["word_start"], m["word_end"]))
    _assert("Контент" in span and "ролики" in span, f"окно на «контент..ролики»: {span!r}", errors)


def test_sequential_cursor(errors):
    print("\n-- sequential-курсор: повторное слово не откатывает назад --")
    # «рабочих» встречается дважды: «несколько рабочих штук» и «рабочие инструменты»(рабочие≠рабочих)
    # «которые/которая» и «я» повторяются. Матчим scene по «рабочие инструменты» ОТ курсора
    # после первой «рабочих» — должно найти ВТОРОЕ вхождение, не первое.
    first = al.align_beat_to_words("несколько рабочих штук", _WORDS)
    second = al.align_beat_to_words("пару клиентов попросили рабочие инструменты", _WORDS,
                                    start_idx=first["word_end"])
    _assert(second["word_start"] >= first["word_end"],
            f"второй beat ({second['word_start']}) не раньше конца первого ({first['word_end']})", errors)
    span = " ".join(_WORDS[i]["word"] for i in range(second["word_start"], second["word_end"]))
    _assert("инструменты" in span, f"нашёл правильное окно: {span!r}", errors)


def test_unmatched(errors):
    print("\n-- чужие слова → unmatched/low, индексы не случайные --")
    m = al.align_beat_to_words("картинг глэмпинг трасса гонки", _WORDS)
    _assert(m["status"] in ("unmatched", "low_confidence"), f"чужой beat → не aligned (status={m['status']}, conf={m['confidence']})", errors)


def test_align_storyboard(errors):
    print("\n-- align_storyboard_to_words: отчёт + курсор по сценам --")
    scenes = [
        {"id": "scene_01", "script_beat": "Пропал почти на два месяца"},
        {"id": "scene_02", "script_beat": "собрал несколько рабочих штук"},
        {"id": "scene_03", "script_beat": "Голосового ассистента которая ведет дела"},
        {"id": "scene_04", "script_beat": "Контент завод который делает ролики"},
        {"id": "scene_05", "script_beat": "пару клиентов попросили рабочие инструменты"},
        {"id": "scene_06", "script_beat": "Подпишись чтобы не пропустить"},
    ]
    res = al.align_storyboard_to_words(scenes, _WORDS)
    rep = res["report"]
    _assert(rep["matched"] >= 5, f"≥5 из 6 сцен сматчились (matched={rep['matched']}, low={rep['low_confidence']})", errors)
    _assert(rep["matched_ratio"] >= 0.8, f"matched_ratio≥0.8 ({rep['matched_ratio']})", errors)
    # монотонность курсора: time_start сцен неубывающий
    starts = [t["time_start"] for t in res["timings"] if t["time_start"] is not None]
    _assert(starts == sorted(starts), f"таймкоды сцен по возрастанию: {starts}", errors)


def main():
    print("=" * 60 + "\nHF alignment matcher (B-roll sync P0)\n" + "=" * 60)
    errors = []
    for fn in (test_normalize, test_exact_beat, test_trimmed_beat,
               test_sequential_cursor, test_unmatched, test_align_storyboard):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all hf-alignment tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
