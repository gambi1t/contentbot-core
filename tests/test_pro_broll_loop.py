"""TDD for the freeze→loop fix in the pro montage (selfie/avatar layout).

A short B-roll clip in a longer pro segment used to FREEZE its last frame
(tpad=stop_mode=clone) — the "стоп-кадр на 16-й секунде". The fix loops the clip
to fill the slot instead, reusing the proven `-stream_loop -1` + output `-t`
pattern already used by _assemble_split/_assemble_dynamic. These are pure ffmpeg
arg builders; the actual render is verified by a live smoke (ffmpeg behaviour
isn't unit-testable). The avatar's per-segment seek (-ss start) — hence lip-sync —
must stay untouched in the split builder.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import video_assembler as va  # noqa: E402


def test_broll_full_seg_loops_not_freezes():
    args = va._pro_broll_full_seg_args("/x/clip.mp4", 8.0, "/x/out.mp4")
    joined = " ".join(args)
    assert "tpad" not in joined                        # no frozen last frame
    assert "-stream_loop" in args
    assert args[args.index("-stream_loop") + 1] == "-1"
    assert args.index("-stream_loop") < args.index("-i")   # loop precedes the clip input
    assert "-t" in args and "8.000" in args            # output trimmed to slot dur


def test_split_seg_loops_broll_and_preserves_avatar_seek():
    args = va._pro_split_seg_args("/x/broll.mp4", "/x/av.mp4", 11.3, 8.0, "/x/out.mp4")
    joined = " ".join(args)
    assert "tpad" not in joined                        # no frozen last frame
    assert "-stream_loop" in args                      # b-roll (input 0) looped
    assert "[0:v]setpts=PTS-STARTPTS[b];[b][1:v]vstack=inputs=2[outv]" in joined
    assert "11.300" in joined                          # avatar still seeked to seg start (lip-sync pos)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
