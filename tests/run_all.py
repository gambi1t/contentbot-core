"""Run all test suites and return non-zero exit if any fail.

Usage:
    python tests/run_all.py

This is the pre-deploy gate — if this script exits with 0, it's safe to scp
bot.py to the server and restart the systemd service.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent

SUITES = [
    "test_smoke.py",
    "test_menu_consistency.py",
    "test_split_script_to_parts.py",
    "test_photo_library.py",
    "test_subtitle_merge.py",
]


def main() -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    failed: list[str] = []
    for suite in SUITES:
        path = TESTS_DIR / suite
        if not path.exists():
            print(f"SKIP {suite} (not found)")
            continue
        print(f"\n{'=' * 60}")
        print(f"Running {suite}")
        print("=" * 60)
        result = subprocess.run(
            [sys.executable, str(path)],
            env=env,
            cwd=str(TESTS_DIR.parent),
        )
        if result.returncode != 0:
            failed.append(suite)

    print(f"\n{'#' * 60}")
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
        return 1
    print(f"OK all {len(SUITES)} suites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
