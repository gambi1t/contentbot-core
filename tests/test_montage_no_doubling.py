"""B (HyperFrames монтаж): задвоение B-roll-клипа. Раньше при slot > clip ffmpeg
`stream_loop -1 -t slot` ПЕРЕЗАПУСКАЛ клип внутри слота → визуально «один и тот
же B-roll показывается дважды» (Артём 22.06, подтверждено покадрово на 50с-ролике).

Фикс: _broll_fit_strategy выбирает стратегию по соотношению slot/clip:
  - slot ≤ clip → trim (без loop)
  - clip < slot ≤ clip × 1.5 → растяжка PTS (≈ замедление, без перезапуска)
  - slot > clip × 1.5 → растянуть до clip×1.5 + freeze хвостом
  - clip_dur неизвестна → legacy loop (backward-compat)

Run: python tests/test_montage_no_doubling.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import video_assembler as va  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


# ── strategy ──────────────────────────────────────────────────────────────────


def test_trim_when_slot_smaller(errors):
    print("\n-- slot ≤ clip → trim (без loop, без stretch) --")
    _assert(va._broll_fit_strategy(4.0, 5.0)["mode"] == "trim", "4с slot/5с клип → trim", errors)
    _assert(va._broll_fit_strategy(5.0, 5.0)["mode"] == "trim", "равный → trim", errors)


def test_stretch_within_max(errors):
    print("\n-- clip < slot ≤ clip×1.5 → stretch PTS (без перезапуска) --")
    s = va._broll_fit_strategy(7.0, 5.0)  # 50с-ролик кейс Артёма
    _assert(s["mode"] == "stretch", f"7с slot/5с клип → stretch, got {s}", errors)
    _assert(abs(s["stretch_ratio"] - 1.4) < 0.01, f"ratio≈1.4, got {s['stretch_ratio']}", errors)
    _assert(va._broll_fit_strategy(7.5, 5.0)["mode"] == "stretch", "на границе 1.5 → ещё stretch", errors)


def test_stretch_freeze_beyond_max(errors):
    print("\n-- slot > clip×1.5 → stretch до 1.5x + freeze хвостом (без задвоения) --")
    s = va._broll_fit_strategy(10.0, 5.0)  # 2x slot
    _assert(s["mode"] == "stretch_freeze", f"10с/5с → stretch_freeze, got {s['mode']}", errors)
    _assert(abs(s["stretch_ratio"] - 1.5) < 0.01, "ratio clamped 1.5", errors)
    _assert(abs(s["stretched_dur"] - 7.5) < 0.01, "stretched_dur=clip×1.5=7.5", errors)
    _assert(abs(s["freeze_dur"] - 2.5) < 0.01, f"freeze_dur=10-7.5=2.5, got {s['freeze_dur']}", errors)


def test_unknown_clip_dur_legacy(errors):
    print("\n-- clip_dur=None → mode=loop (backward-compat) --")
    _assert(va._broll_fit_strategy(7.0, None)["mode"] == "loop", "None → loop", errors)
    _assert(va._broll_fit_strategy(7.0, 0)["mode"] == "loop", "0 → loop", errors)


# ── ffmpeg args contract ─────────────────────────────────────────────────────


def _args(slot, clip_dur):
    return va._pro_broll_full_seg_args("/tmp/broll.mp4", slot, "/tmp/out.mp4", clip_dur=clip_dur)


def test_args_no_stream_loop_when_clip_dur_known(errors):
    print("\n-- ffmpeg args: clip_dur известна → НЕТ stream_loop (нет задвоения) --")
    for slot in (4.0, 6.0, 7.0, 10.0):
        a = _args(slot, 5.0)
        _assert("-stream_loop" not in a,
                f"slot={slot} clip=5 → без stream_loop (got {a[:6]})", errors)


def test_args_use_setpts_when_stretch(errors):
    print("\n-- ffmpeg args: при stretch есть setpts*PTS --")
    a = _args(7.0, 5.0)
    flt = next((a[i + 1] for i, x in enumerate(a) if x == "-filter:v"), "")
    _assert("setpts" in flt and "PTS" in flt, f"setpts в фильтре, got {flt!r}", errors)


def test_args_use_tpad_when_stretch_freeze(errors):
    print("\n-- ffmpeg args: при stretch_freeze есть setpts + tpad freeze --")
    a = _args(10.0, 5.0)
    flt = next((a[i + 1] for i, x in enumerate(a) if x == "-filter:v"), "")
    _assert("setpts" in flt and "tpad" in flt and "stop_mode=clone" in flt,
            f"setpts+tpad clone, got {flt!r}", errors)


def test_args_trim_when_slot_small(errors):
    print("\n-- ffmpeg args: trim (slot ≤ clip) — без filter:v, просто -t --")
    a = _args(4.0, 5.0)
    _assert("-filter:v" not in a, f"trim → без -filter:v, got {a[:8]}", errors)
    _assert("-stream_loop" not in a, "trim → без stream_loop", errors)


def test_args_legacy_loop_when_unknown(errors):
    print("\n-- ffmpeg args: clip_dur=None → старый stream_loop (backward-compat) --")
    a = va._pro_broll_full_seg_args("/tmp/x.mp4", 7.0, "/tmp/o.mp4", clip_dur=None)
    _assert("-stream_loop" in a, "None → stream_loop (legacy)", errors)


def main() -> int:
    print("=" * 60 + "\nMontage no-doubling (B): fit-strategy + ffmpeg args\n" + "=" * 60)
    errors: list = []
    for fn in (test_trim_when_slot_smaller, test_stretch_within_max,
               test_stretch_freeze_beyond_max, test_unknown_clip_dur_legacy,
               test_args_no_stream_loop_when_clip_dur_known, test_args_use_setpts_when_stretch,
               test_args_use_tpad_when_stretch_freeze, test_args_trim_when_slot_small,
               test_args_legacy_loop_when_unknown):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all montage-no-doubling tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
