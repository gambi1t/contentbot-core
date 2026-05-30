"""Test C1 fix: montage plan and assembler clips come from ONE source (mode).

Bug C1 (29 May 2026, caught by adversarial review): the pro/ai montage plan was
built from `glob("broll_*.mp4")` (root only) while the assembler pulled clips via
`_find_broll(mix)` (root + autobroll/ + hyperframes/). Result: generated graphics
(in subdirs, root empty) -> plan had 0 broll segments -> all graphics dropped, final
video = bare avatar. Or with SMM clips present -> index mismatch / interleave.

Fix: `_find_broll(proj_dir, mode)` is the single source for BOTH plan count and
assembler clips. This test locks:
  1. _find_broll returns the right clips per mode (real/ai/hf/mix).
  2. build_bookend_montage_plan(D, N) produces N broll segments for N>0,
     and the degenerate single-avatar plan for N==0 (the bug symptom).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from video_assembler import _find_broll, build_bookend_montage_plan  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _touch_mp4(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 2048)


def test_find_broll_modes(errors: list[str]) -> None:
    print("\n-- _find_broll separates namespaces --")
    tmp = Path(tempfile.mkdtemp(prefix="broll_ns_"))
    try:
        # SMM real clips (root)
        _touch_mp4(tmp / "broll_01.mp4")
        _touch_mp4(tmp / "broll_02.mp4")
        # Remotion (autobroll/)
        _touch_mp4(tmp / "autobroll" / "auto_01.mp4")
        # HyperFrames (hyperframes/)
        _touch_mp4(tmp / "hyperframes" / "hf_01.mp4")
        _touch_mp4(tmp / "hyperframes" / "hf_02.mp4")
        _touch_mp4(tmp / "hyperframes" / "hf_03.mp4")

        real = _find_broll(tmp, "real")
        ai = _find_broll(tmp, "ai")
        hf = _find_broll(tmp, "hf")
        mix = _find_broll(tmp, "mix")

        _assert(len(real) == 2, f"real -> 2 SMM clips (got {len(real)})", errors)
        _assert(all("broll_" in p.name for p in real), "real -> only broll_*", errors)
        _assert(len(ai) == 1, f"ai -> 1 autobroll clip (got {len(ai)})", errors)
        _assert(all(p.parent.name == "autobroll" for p in ai), "ai -> only autobroll/", errors)
        _assert(len(hf) == 3, f"hf -> 3 hyperframes clips (got {len(hf)})", errors)
        _assert(all(p.parent.name == "hyperframes" for p in hf), "hf -> only hyperframes/", errors)
        _assert(len(mix) == 6, f"mix -> all 6 (got {len(mix)})", errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mode_priority_logic(errors: list[str]) -> None:
    print("\n-- mode-priority (hf > ai > real > mix), like card_asm_go --")

    def pick_mode(tmp: Path) -> str:
        if _find_broll(tmp, "hf"):
            return "hf"
        if _find_broll(tmp, "ai"):
            return "ai"
        if _find_broll(tmp, "real"):
            return "real"
        return "mix"

    # HyperFrames present -> hf wins even if SMM clips exist
    tmp = Path(tempfile.mkdtemp(prefix="broll_pri_"))
    try:
        _touch_mp4(tmp / "broll_01.mp4")
        _touch_mp4(tmp / "hyperframes" / "hf_01.mp4")
        _assert(pick_mode(tmp) == "hf", f"hf wins over real (got {pick_mode(tmp)})", errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Only Remotion -> ai
    tmp = Path(tempfile.mkdtemp(prefix="broll_pri_"))
    try:
        _touch_mp4(tmp / "autobroll" / "auto_01.mp4")
        _assert(pick_mode(tmp) == "ai", f"ai when only autobroll (got {pick_mode(tmp)})", errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Only SMM -> real
    tmp = Path(tempfile.mkdtemp(prefix="broll_pri_"))
    try:
        _touch_mp4(tmp / "broll_01.mp4")
        _assert(pick_mode(tmp) == "real", f"real when only SMM (got {pick_mode(tmp)})", errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Empty -> mix (fallback)
    tmp = Path(tempfile.mkdtemp(prefix="broll_pri_"))
    try:
        _assert(pick_mode(tmp) == "mix", f"mix when empty (got {pick_mode(tmp)})", errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_plan_count_matches(errors: list[str]) -> None:
    print("\n-- build_bookend_montage_plan: N clips -> N broll segments --")
    # The crux of C1: plan must reflect the actual clip count.
    plan6 = build_bookend_montage_plan(30.0, 6)
    broll_segs = [s for s in plan6 if s.get("broll_index") is not None]
    _assert(len(broll_segs) == 6, f"6 clips -> 6 broll segments (got {len(broll_segs)})", errors)

    # Degenerate case (the bug symptom): 0 clips -> single avatar_full, graphics lost.
    plan0 = build_bookend_montage_plan(30.0, 0)
    broll0 = [s for s in plan0 if s.get("broll_index") is not None]
    _assert(len(broll0) == 0, f"0 clips -> 0 broll segments (got {len(broll0)})", errors)
    _assert(
        len(plan0) == 1 and plan0[0]["layout"] == "avatar_full",
        "0 clips -> single avatar_full (proves why mismatch dropped graphics)",
        errors,
    )


def main() -> int:
    print("=" * 60)
    print("test_broll_namespace_modes (C1 fix)")
    print("=" * 60)
    errors: list[str] = []
    test_find_broll_modes(errors)
    test_mode_priority_logic(errors)
    test_plan_count_matches(errors)
    print()
    if errors:
        print(f"FAIL: {len(errors)} assertion(s) failed")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
