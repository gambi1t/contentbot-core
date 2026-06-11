"""Pre-deploy check — run before every scp + systemctl restart.

Combines:
1. ruff lint (undefined vars, scoping, unused imports)
2. Menu consistency (all callbacks have handlers, menus are complete)
3. Smoke test (syntax, required functions exist)
4. split_script_to_parts regression suite (Apr 15 2026 mid-sentence split bug)
5. Photo library helpers (explicit Ken Burns path, not hidden fallback)
6. Subtitle merge (Apr 15 2026 "Меджорни" / "V8.1" whisper fragment bug)

Usage: python pre_deploy.py
Exit code 0 = safe to deploy, 1 = fix issues first.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def run_step(name: str, cmd: list[str]) -> bool:
    """Run a step, return True if passed."""
    print(f"\n{'=' * 50}")
    print(f"  {name}")
    print(f"{'=' * 50}")
    # Force UTF-8 for all subprocesses so Cyrillic test output doesn't crash
    # on Windows cp1251 consoles.
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    result = subprocess.run(cmd, cwd=str(ROOT), env=env)
    return result.returncode == 0


def main() -> int:
    results = []

    # 1. Ruff lint
    passed = run_step(
        "STEP 1: ruff lint",
        [sys.executable, "-m", "ruff", "check",
         "bot.py", "video_assembler.py", "subtitle_burner.py", "launch_monitor.py",
         "--config", "ruff.toml"],
    )
    results.append(("ruff lint", passed))

    # 2. Menu consistency
    passed = run_step(
        "STEP 2: Menu consistency",
        [sys.executable, "tests/test_menu_consistency.py"],
    )
    results.append(("menu check", passed))

    # 3. Smoke test
    passed = run_step(
        "STEP 3: Smoke test",
        [sys.executable, "tests/test_smoke.py"],
    )
    results.append(("smoke test", passed))

    # 4. split_script_to_parts regression
    passed = run_step(
        "STEP 4: split_script_to_parts regression",
        [sys.executable, "tests/test_split_script_to_parts.py"],
    )
    results.append(("splitter regression", passed))

    # 5. Photo library helpers
    passed = run_step(
        "STEP 5: Photo library helpers",
        [sys.executable, "tests/test_photo_library.py"],
    )
    results.append(("photo library", passed))

    # 6. Subtitle merge (whisper fragment re-joiner)
    passed = run_step(
        "STEP 6: Subtitle merge",
        [sys.executable, "tests/test_subtitle_merge.py"],
    )
    results.append(("subtitle merge", passed))

    # Summary
    print(f"\n{'=' * 50}")
    print("  RESULTS")
    print(f"{'=' * 50}")
    all_passed = True
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if not ok:
            all_passed = False

    if all_passed:
        print("\n>>> All checks passed. Safe to deploy!")
        return 0
    else:
        print("\n>>> FIX ISSUES before deploying!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
