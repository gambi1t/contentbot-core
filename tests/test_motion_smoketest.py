"""TDD: motion_smoketest Python-обёртка (Phase 1 Step 5).

Прогон tools/motion_smoketest.mjs на 4 фикстур-сценах из tools/test_fixtures_motion/:
  fixture_motion_healthy.html        — большой bar едет + меняет цвет → ok
  fixture_motion_warning.html        — тонкая полоса 0.1% safe-area → warning
  fixture_motion_static.html         — timeline без onUpdate (scene_04 sim) → fail
  fixture_no_timeline.html           — нет window.__timelines → no_timeline

Тест требует node + локально установленный puppeteer + pngjs + pixelmatch
(в hyperframes_assets/package.json). Если node нет — тесты skipped.

Run: python tests/test_motion_smoketest.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))
from motion_smoketest import MotionVerdict, check_motion  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "tools" / "test_fixtures_motion"


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _skip_if_no_node():
    """Если node не установлен — печатаем skip и возвращаем True."""
    if shutil.which("node") is None:
        print("(SKIP — node не найден в PATH)")
        return True
    return False


def test_verdict_dataclass(errors):
    print("\n-- MotionVerdict dataclass + is_blocking --")
    v = MotionVerdict(verdict="ok", ok=True)
    _assert(v.is_blocking is False, "ok не блокирующий", errors)
    v = MotionVerdict(verdict="warning", ok=True)
    _assert(v.is_blocking is False, "warning не блокирующий", errors)
    v = MotionVerdict(verdict="fail", ok=False)
    _assert(v.is_blocking is True, "fail блокирующий", errors)
    v = MotionVerdict(verdict="no_timeline", ok=False)
    _assert(v.is_blocking is True, "no_timeline блокирующий", errors)


def test_healthy_fixture(errors):
    if _skip_if_no_node():
        return
    print("\n-- fixture_motion_healthy → verdict=ok --")
    p = FIXTURES / "fixture_motion_healthy.html"
    v = check_motion(p)
    _assert(v.verdict == "ok", f"verdict=ok (got {v.verdict}, reason={v.reason!r})", errors)
    _assert(v.ok is True, "ok=True", errors)
    _assert(v.is_blocking is False, "не блокирует pipeline", errors)
    _assert(v.max_diff_pct is not None and v.max_diff_pct > 0.02,
            f"max_diff_pct > 2% (got {v.max_diff_pct})", errors)


def test_warning_fixture(errors):
    if _skip_if_no_node():
        return
    print("\n-- fixture_motion_warning → verdict=warning --")
    p = FIXTURES / "fixture_motion_warning.html"
    v = check_motion(p)
    _assert(v.verdict == "warning",
            f"verdict=warning (got {v.verdict}, max_diff_pct={v.max_diff_pct})", errors)
    _assert(v.is_blocking is False, "warning НЕ блокирует pipeline", errors)


def test_static_fixture(errors):
    """Главный регресс: scene_04-style баг (seek+plain-object без onUpdate-в-DOM)."""
    if _skip_if_no_node():
        return
    print("\n-- fixture_motion_static → verdict=fail (scene_04-style баг) --")
    p = FIXTURES / "fixture_motion_static.html"
    v = check_motion(p)
    _assert(v.verdict == "fail", f"verdict=fail (got {v.verdict})", errors)
    _assert(v.is_blocking is True, "fail блокирует pipeline", errors)
    _assert(v.max_diff_pct == 0 or (v.max_diff_pct or 0) < 0.001,
            f"max_diff_pct ~0 (got {v.max_diff_pct})", errors)


def test_no_timeline_fixture(errors):
    if _skip_if_no_node():
        return
    print("\n-- fixture_no_timeline → verdict=no_timeline --")
    p = FIXTURES / "fixture_no_timeline.html"
    v = check_motion(p)
    _assert(v.verdict == "no_timeline",
            f"verdict=no_timeline (got {v.verdict})", errors)
    _assert(v.is_blocking is True, "no_timeline блокирует pipeline", errors)


def test_missing_file(errors):
    print("\n-- несуществующий файл → verdict=error --")
    v = check_motion("/tmp/__no_such_file_motion.html")
    _assert(v.verdict == "error", f"verdict=error (got {v.verdict})", errors)
    _assert(v.is_blocking is True, "error блокирует pipeline", errors)


def main():
    print("=" * 60)
    print("test_motion_smoketest (Phase 1 step 5)")
    print("=" * 60)
    errors = []
    test_verdict_dataclass(errors)
    test_healthy_fixture(errors)
    test_warning_fixture(errors)
    test_static_fixture(errors)
    test_no_timeline_fixture(errors)
    test_missing_file(errors)
    print()
    if errors:
        print(f"FAIL: {len(errors)} assertion(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
