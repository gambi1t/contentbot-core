"""TDD for узел D — narrative assembly in broll/assembler.py.

Default (narrative=False) = current behaviour byte-identical (AUTO/HF/AUTO_HF):
each clip capped to MAX_SEG_SEC=5s, segments cycled to fill the voiceover.
narrative=True (Seedance fullscreen): play clips FULL length (10s multi-shot
not truncated) in a single ordered pass, no cycling (last trimmed by final -t).
Pure helpers — unit-testable; the real render is verified by a live smoke.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import broll.assembler as A  # noqa: E402


# ── _seg_len: cap vs full ────────────────────────────────────────────────────

def test_seg_len_default_caps_at_max():
    assert A._seg_len(10.0, narrative=False) == A.MAX_SEG_SEC   # 10s → capped to 5s
    assert A._seg_len(3.0, narrative=False) == 3.0
    assert A._seg_len(0.4, narrative=False) == A.MIN_SEG_SEC     # floored


def test_seg_len_narrative_keeps_full_length():
    assert A._seg_len(10.0, narrative=True) == 10.0             # no cap — full multi-shot
    assert A._seg_len(0.4, narrative=True) == A.MIN_SEG_SEC      # still floored


# ── _build_sequence: cycle vs single ordered pass ───────────────────────────

def test_build_sequence_default_cycles_to_cover():
    segs = [(Path("a.mp4"), 5.0), (Path("b.mp4"), 5.0)]
    seq = A._build_sequence(segs, voiceover_dur=30.0, narrative=False)
    assert len(seq) == 7                          # cycles to cover 30 + MAX_SEG_SEC
    assert seq[0].name == "a.mp4" and seq[2].name == "a.mp4"   # repeats (round-robin)


def test_build_sequence_narrative_single_pass_in_order():
    segs = [(Path(f"{c}.mp4"), 10.0) for c in "abcde"]   # 5 × 10s
    seq = A._build_sequence(segs, voiceover_dur=30.0, narrative=True)
    assert [p.name for p in seq] == ["a.mp4", "b.mp4", "c.mp4"]   # 3×10=30, in order, no repeat


def test_build_sequence_narrative_uses_all_when_short():
    segs = [(Path("a.mp4"), 10.0), (Path("b.mp4"), 10.0)]   # 20s < 30s voiceover
    seq = A._build_sequence(segs, voiceover_dur=30.0, narrative=True)
    assert [p.name for p in seq] == ["a.mp4", "b.mp4"]      # all clips once, NO cycling


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
