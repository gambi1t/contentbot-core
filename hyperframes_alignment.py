"""Выравнивание HF-сцен по реальному таймкоду речи (B-roll sync, P0).

Проблема (Артём 23.06): HF-сцены контент-специфичны (каждая под свой script_beat),
но монтаж раскладывал их РАВНОМЕРНО (build_bookend_montage_plan) или по оценке
Claude — без word-level таймкодов Whisper. Клип «голосовой ассистент» выскакивал
не там, где он произносится.

Решение (CTO+GPT-5 ревью): детерминированный matcher script_beat → selfie_words.
LLM НЕ timing-authority — таймкоды берём фактические. Якорный матчинг (редкие/
длинные слова beat'а ищем в потоке слов от курсора по порядку — это устойчиво к
лёгкому перефразу Claude и не откатывается назад на повторных словах).

В нашем флоу правка транскрипта word-count-locked (apply_user_edits) → таймкоды
не уезжают; монтаж стартует с оригинального селфи → таймлайн слов == таймлайн
монтажа (timebase чистый). Поэтому достаточно P0-матчинга без слоёв original/edited.

Чистый модуль (unit-tested), без тяжёлых зависимостей.
"""
from __future__ import annotations

import math

# Короткие частые слова, которые не несут смысла для якорения (даже если ≥4 симв).
_STOP = frozenset({
    "меня", "тебя", "себя", "когда", "потому", "очень", "можно", "чтобы",
    "этот", "эта", "это", "эти", "того", "тоже", "ещё", "еще", "уже", "там",
    "если", "была", "было", "были", "быть", "есть", "весь", "всё", "все",
    "как", "что", "так", "вот", "над", "под", "при", "без", "для", "про",
})

# Пороги уверенности (доля найденных якорей beat'а) — из GPT-5 ревью.
ALIGNED_MIN = 0.72
LOW_MIN = 0.50


def normalize_ru_text(text: str) -> list[str]:
    """text → токены: lowercase, ё→е, без пунктуации (буквы/цифры остаются)."""
    out: list[str] = []
    cur: list[str] = []
    for ch in (text or "").lower().replace("ё", "е"):
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _anchors(beat_tokens: list[str]) -> list[str]:
    """Якоря beat'а — различимые слова (len≥4, не стоп). Если их мало — все токены."""
    a = [t for t in beat_tokens if len(t) >= 4 and t not in _STOP]
    return a if len(a) >= 2 else list(beat_tokens)


def align_beat_to_words(beat: str, words: list[dict], start_idx: int = 0,
                        lookahead: int = 60) -> dict:
    """Найти окно [word_start, word_end) в words[start_idx:], где произносится beat.

    Якорный матчинг: ищем токены-якоря beat'а в окне поиска ОТ start_idx (по порядку
    — не откатываемся назад на повторных словах). Окно = от первого до последнего
    найденного якоря. confidence = доля найденных якорей.

    Возвращает dict: word_start, word_end (абс. индексы в words), time_start,
    time_end (сек), confidence (0..1), method, status (aligned/low_confidence/unmatched).
    Если ничего не нашли — status=unmatched, индексы None.
    """
    n = len(words)
    beat_tokens = normalize_ru_text(beat)
    if not beat_tokens or start_idx >= n:
        return {"word_start": None, "word_end": None, "time_start": None,
                "time_end": None, "confidence": 0.0, "method": "anchor",
                "status": "unmatched"}

    anchors = _anchors(beat_tokens)
    region_end = min(n, start_idx + lookahead)
    # нормализованные токены слов в зоне поиска (по одному токену на слово;
    # если whisper-«слово» содержит пробел/пунктуацию — берём первый токен)
    region_norm: list[str] = []
    for i in range(start_idx, region_end):
        toks = normalize_ru_text(words[i].get("word", ""))
        region_norm.append(toks[0] if toks else "")

    matched_rel: list[int] = []          # относительные индексы найденных якорей
    seen_anchor: set[str] = set()
    search_from = 0
    for a in anchors:
        # первое вхождение якоря a в region начиная с search_from (по порядку)
        for rel in range(search_from, len(region_norm)):
            if region_norm[rel] == a:
                matched_rel.append(rel)
                seen_anchor.add(a)
                search_from = rel + 1     # следующий якорь — дальше по потоку
                break

    uniq_anchor_targets = len(set(anchors))
    confidence = (len(seen_anchor) / uniq_anchor_targets) if uniq_anchor_targets else 0.0

    if not matched_rel:
        return {"word_start": None, "word_end": None, "time_start": None,
                "time_end": None, "confidence": 0.0, "method": "anchor",
                "status": "unmatched"}

    word_start = start_idx + matched_rel[0]
    word_end = start_idx + matched_rel[-1] + 1     # полуинтервал [start, end)
    status = ("aligned" if confidence >= ALIGNED_MIN
              else "low_confidence" if confidence >= LOW_MIN else "unmatched")
    return {
        "word_start": word_start,
        "word_end": word_end,
        "time_start": float(words[word_start]["start"]),
        "time_end": float(words[word_end - 1]["end"]),
        "confidence": round(confidence, 3),
        "method": "anchor",
        "status": status,
    }


def align_storyboard_to_words(scenes: list[dict], words: list[dict]) -> dict:
    """Выровнять все сцены раскадровки по словам (последовательный курсор).

    scenes — из storyboard (в порядке, у каждой script_beat). words — selfie_words.
    Возвращает {"timings": [по-сценам], "report": {агрегат}}. Курсор двигается
    только на aligned/low_confidence (unmatched не съедает поток — другая сцена
    ещё может найтись дальше)."""
    timings: list[dict] = []
    cursor = 0
    n = len(words)
    matched = low = unmatched = 0
    for sc in scenes:
        sid = sc.get("id")
        beat = sc.get("script_beat", "") or ""
        m = align_beat_to_words(beat, words, cursor)
        rec = {"scene_id": sid, "beat": beat[:80], **m}
        timings.append(rec)
        if m["status"] == "aligned":
            matched += 1
            cursor = min(n, m["word_end"])
        elif m["status"] == "low_confidence":
            low += 1
            cursor = min(n, m["word_end"])
        else:
            unmatched += 1
            # курсор НЕ двигаем — следующая сцена ищет с той же позиции
    total = len(scenes) or 1
    report = {
        "scenes": len(scenes),
        "matched": matched,
        "low_confidence": low,
        "unmatched": unmatched,
        "matched_ratio": round((matched + low) / total, 3),
        "avg_confidence": round(
            sum(t["confidence"] for t in timings) / total, 3),
    }
    return {"timings": timings, "report": report}
