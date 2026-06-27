"""TDD: cap-aware верх головы аватара — кепка/шапка не режется в split-окне.

Старый метод (`face_top - 0.35*fh`) недооценивает высокий головной убор → верх
кепки оказывается выше окна кропа → срез (баг с записи SMM 25.06, аватар в кепке).
GrabCut (засеян лицом) находит верх СУБЪЕКТА; `_pick_head_top` принимает его, если
он правдоподобен, иначе fallback на старый расчёт (graceful — поведение не хуже).

Сама GrabCut-детекция (image-based) проверена визуально на реальном кадре аватара
(линия легла на верх кепки) — здесь юнит-тестим чистую логику ВЫБОРА/фолбэка.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import video_assembler as VA  # noqa: E402

# Реальные числа с кадра аватара 25.06: лицо fy=643 fh=227 → face_lift_top=564,
# GrabCut верх кепки = 482 (выше → кепку видно).
_FY, _FH, _FACE_LIFT = 643, 227, 564


def test_grabcut_subject_top_used_when_above_facelift():
    # GrabCut нашёл кепку выше расчётного face+lift → берём его (кепка влезет).
    assert VA._pick_head_top(482, _FACE_LIFT, _FY, _FH) == 482


def test_fallback_when_grabcut_none():
    # GrabCut недоступен/не нашёл → старый расчёт (поведение не хуже прежнего).
    assert VA._pick_head_top(None, _FACE_LIFT, _FY, _FH) == _FACE_LIFT


def test_fallback_when_grabcut_below_facelift():
    # GrabCut вернул НИЖЕ face+lift (схватил лицо, не кепку) — не доверяем.
    assert VA._pick_head_top(600, _FACE_LIFT, _FY, _FH) == _FACE_LIFT


def test_fallback_when_grabcut_implausibly_high():
    # Слишком высоко (> 1.5*fh над лицом = схватил фон) → fallback, не задираем кадр.
    lo = _FY - int(1.5 * _FH)  # 303
    assert VA._pick_head_top(lo - 50, _FACE_LIFT, _FY, _FH) == _FACE_LIFT


def test_grabcut_accepted_at_plausible_lower_bound():
    lo = _FY - int(1.5 * _FH)  # 303 — самый высокий допустимый головной убор
    assert VA._pick_head_top(lo, _FACE_LIFT, _FY, _FH) == lo


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
