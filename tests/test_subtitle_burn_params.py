"""Regression lock for subtitle burn ffmpeg parameters.

История (замерено 18 июня 2026, см. reference_selfie_quality_telegram_compression):
burn однажды сменили с `veryfast/crf20` на `medium/crf15` ради НУЛЕВОГО
прироста качества (SSIM 0.988 vs 0.989). Цена: 20-сек ролик кодировался
259 сек (~12× realtime). Разговорный selfie 40сек–минута 1080p/60 при `medium`
≈ 770 сек > timeout 600 → ffmpeg ПАДАЛ, ролик не собирался.

Этот тест — замок: burn должен оставаться `veryfast` + `crf 20` + timeout 900,
чтобы минутный ролик укладывался в лимит, а качество оставалось прозрачным.

Run: python tests/test_subtitle_burn_params.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from subtitle_burner import build_burn_cmd, BURN_PRESET, BURN_CRF, BURN_TIMEOUT  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    if not cond:
        errors.append(f"FAIL {msg}")
        print(f"  FAIL {msg}")
    else:
        print(f"  OK {msg}")


def test_constants(errors: list[str]) -> None:
    print("\n-- constants: veryfast / 20 / 900 --")
    _assert(BURN_PRESET == "veryfast", f"preset veryfast (got {BURN_PRESET!r})", errors)
    _assert(str(BURN_CRF) == "20", f"crf 20 (got {BURN_CRF!r})", errors)
    _assert(BURN_TIMEOUT >= 900, f"timeout >= 900 (got {BURN_TIMEOUT})", errors)


def test_cmd_uses_veryfast_not_medium(errors: list[str]) -> None:
    """CRITICAL regression: never silently slip back to `medium`."""
    print("\n-- cmd: preset veryfast, NOT medium --")
    cmd = build_burn_cmd("in.mp4", "ass='subs.ass'", "out.mp4")
    _assert("veryfast" in cmd, f"veryfast present (got {cmd})", errors)
    _assert("medium" not in cmd, "regression: 'medium' must be absent", errors)
    i = cmd.index("-preset")
    _assert(cmd[i + 1] == "veryfast", f"-preset veryfast (got {cmd[i + 1]!r})", errors)


def test_cmd_crf20_not_15(errors: list[str]) -> None:
    print("\n-- cmd: crf 20, NOT 15 --")
    cmd = build_burn_cmd("in.mp4", "ass='subs.ass'", "out.mp4")
    i = cmd.index("-crf")
    _assert(cmd[i + 1] == "20", f"-crf 20 (got {cmd[i + 1]!r})", errors)
    _assert("15" not in cmd, "regression: crf '15' must be absent", errors)


def test_cmd_structure(errors: list[str]) -> None:
    print("\n-- cmd: core structure intact --")
    vf = "ass='subs.ass'"
    cmd = build_burn_cmd("in.mp4", vf, "out.mp4")
    _assert(cmd[0] == "ffmpeg", f"starts ffmpeg (got {cmd[0]!r})", errors)
    _assert("libx264" in cmd, "libx264 codec", errors)
    _assert("yuv420p" in cmd, "yuv420p pix_fmt", errors)
    _assert(vf in cmd, "vf filter passed through verbatim", errors)
    _assert("in.mp4" in cmd, "input path present", errors)
    _assert("out.mp4" in cmd, "output path present", errors)
    ai = cmd.index("-c:a")
    _assert(cmd[ai + 1] == "copy", f"audio copy (got {cmd[ai + 1]!r})", errors)
    _assert("+faststart" in cmd, "+faststart for streaming", errors)


def test_cmd_input_before_filter(errors: list[str]) -> None:
    """-i input must precede -vf so ffmpeg parses correctly."""
    print("\n-- cmd: -i before -vf --")
    cmd = build_burn_cmd("in.mp4", "ass='x.ass'", "out.mp4")
    _assert(cmd.index("-i") < cmd.index("-vf"), "-i before -vf", errors)
    _assert(cmd[cmd.index("-i") + 1] == "in.mp4", "input follows -i", errors)


def main() -> int:
    print("=" * 60)
    print("subtitle_burner: burn ffmpeg params (regression lock)")
    print("=" * 60)

    errors: list[str] = []
    test_constants(errors)
    test_cmd_uses_veryfast_not_medium(errors)
    test_cmd_crf20_not_15(errors)
    test_cmd_structure(errors)
    test_cmd_input_before_filter(errors)

    print("\n" + "=" * 60)
    if errors:
        print(f"Found {len(errors)} failure(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK all burn param tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
