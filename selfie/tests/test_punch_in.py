"""TDD for selfie.punch_in.plan_punch_in_segments — pure segment planner.

Punch-in монтаж: режем минутное селфи на сегменты по границам предложений
(из Whisper word-timestamps) и назначаем чередующийся зум, чтобы статичная
говорящая голова стала динамичной (интервью-эффект из одной камеры).

Эти тесты покрывают ТОЛЬКО чистый планировщик — без ffmpeg, без лица.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from selfie.punch_in import plan_punch_in_segments

ZL = (1.0, 1.08, 1.14)


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def _continuous(segs):
    """Сегменты непрерывны и не пересекаются."""
    for a, b in zip(segs, segs[1:]):
        if abs(a["end"] - b["start"]) > 1e-6:
            return False
    return True


# ── Edge: пусто ──────────────────────────────────────────────────────────────

def test_empty_words_returns_empty():
    assert plan_punch_in_segments([], zoom_levels=ZL) == []


# ── Одно короткое предложение ────────────────────────────────────────────────

def test_single_short_sentence_one_segment():
    words = [_w("Привет.", 0.0, 1.0), _w("Как.", 1.0, 2.0)]
    # Оба заканчиваются точкой, но < min_seg → склеиваются в один сегмент
    segs = plan_punch_in_segments(words, min_seg=4.0, max_seg=12.0, zoom_levels=ZL)
    assert len(segs) == 1
    assert segs[0]["start"] == 0.0
    assert segs[0]["end"] == 2.0
    assert segs[0]["zoom"] == ZL[0]  # первый сегмент = базовая крупность


# ── Длинное предложение без пунктуации в середине → дробится ─────────────────

def test_long_segment_is_split():
    # 20 секунд непрерывной речи без точек → должно разбиться
    words = [_w(f"слово{i}", float(i), float(i + 1)) for i in range(20)]
    segs = plan_punch_in_segments(words, min_seg=4.0, max_seg=12.0, zoom_levels=ZL)
    assert len(segs) >= 2
    for s in segs:
        assert s["end"] - s["start"] <= 12.0 + 1e-6, f"сегмент длиннее max_seg: {s}"


# ── Нет пунктуации, но есть паузы → группировка по паузам ────────────────────

def test_grouping_by_pauses_when_no_punctuation():
    # 3 блока речи, разделённые паузами > 0.6с, без точек
    words = [
        _w("раз", 0.0, 1.0), _w("два", 1.0, 2.0),       # блок 1
        _w("три", 3.0, 4.0), _w("четыре", 4.0, 5.0),    # пауза 1с → блок 2
        _w("пять", 6.5, 7.5), _w("шесть", 7.5, 8.5),    # пауза 1.5с → блок 3
    ]
    segs = plan_punch_in_segments(words, min_seg=1.0, max_seg=12.0, zoom_levels=ZL)
    assert len(segs) == 3


# ── Несколько коротких предложений → склейка до min_seg ──────────────────────

def test_short_sentences_merged_to_min_seg():
    words = [
        _w("Раз.", 0.0, 1.0), _w("Два.", 1.0, 2.0), _w("Три.", 2.0, 3.0),
        _w("Четыре.", 3.0, 4.0), _w("Пять.", 4.0, 5.0), _w("Шесть.", 5.0, 6.0),
        _w("Семь.", 6.0, 7.0), _w("Восемь.", 7.0, 8.0),
    ]
    segs = plan_punch_in_segments(words, min_seg=4.0, max_seg=12.0, zoom_levels=ZL)
    # 8 предложений по 1с → склеятся в сегменты ≥ 4с
    for s in segs[:-1]:  # последний хвост может быть короче
        assert s["end"] - s["start"] >= 4.0 - 1e-6


# ── Инвариант: соседние zoom различаются ─────────────────────────────────────

def test_adjacent_zoom_differs():
    words = [_w(f"слово{i}.", float(i), float(i + 1)) for i in range(40)]
    segs = plan_punch_in_segments(words, min_seg=4.0, max_seg=8.0, zoom_levels=ZL)
    assert len(segs) >= 3
    for a, b in zip(segs, segs[1:]):
        assert a["zoom"] != b["zoom"], f"соседние zoom равны: {a['zoom']}"


# ── Непрерывность таймлайна (критично для субтитров) ─────────────────────────

def test_timeline_is_continuous_and_covers_all():
    words = [_w(f"w{i}.", float(i), float(i + 1)) for i in range(30)]
    segs = plan_punch_in_segments(words, min_seg=4.0, max_seg=10.0, zoom_levels=ZL)
    assert segs[0]["start"] == words[0]["start"]
    assert segs[-1]["end"] == words[-1]["end"]
    assert _continuous(segs), "есть дыра/нахлёст между сегментами"


# ── Короткий хвост приклеивается, не остаётся огрызком ───────────────────────

def test_short_tail_merged_into_previous():
    # 13 секунд: 12с блок + 1с хвост → хвост не должен быть отдельным <min_seg
    words = [_w(f"w{i}", float(i), float(i + 1)) for i in range(13)]
    segs = plan_punch_in_segments(words, min_seg=4.0, max_seg=12.0, zoom_levels=ZL)
    if len(segs) >= 2:
        # последний сегмент тоже должен быть «вменяемой» длины (не 1с огрызок),
        # либо хвост приклеен к предыдущему
        assert segs[-1]["end"] - segs[-1]["start"] >= 4.0 - 1e-6


# ── zoom_levels из одного элемента → не падать, все 1.0 ──────────────────────

def test_single_zoom_level_graceful():
    words = [_w(f"w{i}.", float(i), float(i + 1)) for i in range(20)]
    segs = plan_punch_in_segments(words, min_seg=4.0, max_seg=8.0, zoom_levels=(1.0,))
    assert all(s["zoom"] == 1.0 for s in segs)


# ── total_duration: покрытие всего ролика (фикс рассинхрона 9 июня) ──────────

def test_total_duration_clamps_first_to_zero_and_last_to_duration():
    # Слова начинаются с 1.5с (пауза в начале) и кончаются на 18с,
    # но видео длится 22с. Сегменты ДОЛЖНЫ покрыть [0, 22].
    words = [_w(f"w{i}.", 1.5 + i, 2.5 + i) for i in range(16)]  # 1.5..17.5
    segs = plan_punch_in_segments(
        words, min_seg=4.0, max_seg=8.0, zoom_levels=ZL, total_duration=22.0
    )
    assert segs[0]["start"] == 0.0, "первый сегмент должен начинаться с 0"
    assert segs[-1]["end"] == 22.0, "последний сегмент должен кончаться на длительности видео"
    assert _continuous(segs), "таймлайн должен остаться непрерывным"


def test_total_duration_none_keeps_word_bounds():
    # Без total_duration — старое поведение (границы по словам).
    words = [_w(f"w{i}.", 1.5 + i, 2.5 + i) for i in range(16)]
    segs = plan_punch_in_segments(words, min_seg=4.0, max_seg=8.0, zoom_levels=ZL)
    assert segs[0]["start"] == 1.5
    assert segs[-1]["end"] == 17.5


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
