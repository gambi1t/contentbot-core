"""TDD for _find_broll supporting the AI-video (Seedance) namespace in the avatar
pipeline (узел 6). Avatar/Pipeline-1 picks the B-roll source by folder namespace;
Seedance clips live in proj/aivideo/ai_NN.mp4 (separate from Remotion autobroll/
and HyperFrames hyperframes/, per the C1 namespace-split fix).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import video_assembler as va  # noqa: E402


def test_find_broll_aivideo_mode(tmp_path):
    (tmp_path / "aivideo").mkdir()
    (tmp_path / "aivideo" / "ai_02.mp4").write_bytes(b"x")
    (tmp_path / "aivideo" / "ai_01.mp4").write_bytes(b"x")
    (tmp_path / "broll_001.mp4").write_bytes(b"x")     # SMM/real — must be excluded
    out = va._find_broll(tmp_path, "aivideo")
    assert [p.name for p in out] == ["ai_01.mp4", "ai_02.mp4"]   # only aivideo, numeric-sorted


def test_find_broll_mix_includes_aivideo(tmp_path):
    (tmp_path / "aivideo").mkdir()
    (tmp_path / "aivideo" / "ai_01.mp4").write_bytes(b"x")
    out = va._find_broll(tmp_path, "mix")
    assert (tmp_path / "aivideo" / "ai_01.mp4") in out


def test_find_broll_other_modes_exclude_aivideo(tmp_path):
    (tmp_path / "aivideo").mkdir()
    (tmp_path / "aivideo" / "ai_01.mp4").write_bytes(b"x")
    (tmp_path / "broll_001.mp4").write_bytes(b"x")
    assert all("aivideo" not in str(p) for p in va._find_broll(tmp_path, "real"))
    assert all("aivideo" not in str(p) for p in va._find_broll(tmp_path, "hf"))


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
