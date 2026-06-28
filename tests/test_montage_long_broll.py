"""TDD: длинный B-roll (30 сек) — режется/подрезается во ВСЕХ режимах,
а не дропается/берётся один кусок (Артём 28.06).

  * fullscreen/smart планировщики: длинный клип ПОДРЕЗАЕТСЯ под окно (trim),
    а не выбрасывается целиком (раньше `body.pop()` → один аватар);
  * seg-args принимают broll_ss → `-ss offset` (нарезка длинного клипа на
    разные окна вместо повтора начала);
  * assembler: offset-курсор пробрасывается в обе ветки (broll_full + split);
  * bot.py план: дубли/вне-диапазона ПЕРЕИСПОЛЬЗУЮТ клипы (round-robin), а не
    конвертятся в avatar.

Запуск: python -m pytest tests/test_montage_long_broll.py -v
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import video_assembler as va  # noqa: E402


# ── Fullscreen: длинный клип подрезается, не дропается ────────────────────

def test_fullscreen_trims_long_clip_not_drop(monkeypatch):
    monkeypatch.setattr(va, "_probe_duration", lambda p: 30.0)  # 30-сек B-roll
    plan = va._plan_fullscreen_only_montage([Path("a.mp4")], [], 3.0, avatar_duration=29.0)
    broll = [s for s in plan if s["broll_index"] is not None]
    assert len(broll) == 1, "длинный клип НЕ должен выпадать (раньше dropped=1)"
    dur = broll[0]["end"] - broll[0]["start"]
    assert 20 < dur < 24, f"клип подрезан под окно ~23с, не дропнут: {dur}"
    assert broll[0]["layout"] == "broll_full"
    # 3с аватар вступление
    assert abs(plan[0]["end"] - 3.0) < 0.01 and plan[0]["layout"] == "avatar_full"


def test_smart_trims_long_clip_not_drop(monkeypatch):
    monkeypatch.setattr(va, "_probe_duration", lambda p: 30.0)
    plan = va._plan_smart_mixed_montage([Path("a.mp4")], [], 3.0, avatar_duration=29.0)
    broll = [s for s in plan if s["broll_index"] is not None]
    assert len(broll) == 1, "smart: длинный клип не дропается"
    dur = broll[0]["end"] - broll[0]["start"]
    assert dur > 15, f"клип подрезан, не дропнут: {dur}"


def test_fullscreen_short_clip_unchanged(monkeypatch):
    # короткий клип (5с) влезает — не трогаем длительность
    monkeypatch.setattr(va, "_probe_duration", lambda p: 5.0)
    plan = va._plan_fullscreen_only_montage([Path("a.mp4")], [], 3.0, avatar_duration=29.0)
    broll = [s for s in plan if s["broll_index"] is not None]
    assert len(broll) == 1
    assert abs((broll[0]["end"] - broll[0]["start"]) - 5.0) < 0.1, "короткий клип не подрезаем"


# ── seg-args: offset-нарезка ──────────────────────────────────────────────

def test_broll_full_seg_args_offset():
    a = va._pro_broll_full_seg_args(Path("c.mp4"), 6.0, Path("o.mp4"), clip_dur=30.0, broll_ss=12.0)
    assert "-ss" in a and "12.000" in a, "нет смещения окна в full-сегменте"


def test_split_seg_args_offset():
    a = va._pro_split_seg_args(Path("c.mp4"), Path("av.mp4"), 0.0, 6.0, Path("o.mp4"),
                               clip_dur=30.0, broll_ss=6.000)
    # -ss встречается дважды: на B-roll (offset) и на аватар (start) — оба ок
    assert a.count("-ss") >= 1 and "6.000" in a, "нет смещения окна в split-сегменте"


def test_broll_full_seg_args_no_offset_when_zero():
    a = va._pro_broll_full_seg_args(Path("c.mp4"), 6.0, Path("o.mp4"), clip_dur=30.0, broll_ss=0.0)
    assert "12.000" not in a, "при ss=0 не должно быть смещения B-roll"


# ── Источник: offset-курсор + переиспользование в плане ───────────────────

def test_assembler_passes_offset_cursor():
    src = (ROOT / "video_assembler.py").read_text(encoding="utf-8")
    assert "broll_cursor" in src, "нет offset-курсора в ассемблере"
    assert "broll_ss=off" in src, "курсор не пробрасывается в seg-args"
    assert "needed_total" in src, "подготовка не по сумме сегментов (нарезка сломана)"


def test_plan_reuses_clips_not_drop_to_avatar():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "Переиспользую broll" in src, "план не переиспользует клипы (round-robin)"
    assert "No broll left, converted to avatar_full" not in src, "остался старый дроп в avatar"


def test_fullscreen_caller_uses_3s_intro():
    # MAJOR из ревью: дефолт сигнатуры перекрывался intro_dur=2.0 из конфига —
    # вызывающий код должен брать fs_intro_dur=3.0 отдельным ключом.
    src = (ROOT / "video_assembler.py").read_text(encoding="utf-8")
    assert 'cfg.get("fs_intro_dur", 3.0)' in src, "fullscreen-интро не 3с в вызывающем коде"


def test_split_loop_fallback_keeps_offset():
    # MINOR из ревью: при clip_dur=None (probe упал) loop-fallback не должен
    # терять offset.
    a = va._pro_split_seg_args(Path("c.mp4"), Path("av.mp4"), 0.0, 6.0, Path("o.mp4"),
                               clip_dur=None, broll_ss=8.0)
    assert "8.000" in a, "split loop-fallback теряет offset (B-roll повторится с начала)"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
