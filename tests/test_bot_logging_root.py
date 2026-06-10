"""Тест видимости логов модулей (10 июня).

Прод-расследование «HF не сделался»: логи hyperframes_broll/scene_scheduler/
video_assembler шли в root logger БЕЗ handlers → невидимы в journald и bot.log
ВСЕГДА (видимым был только "content_bot"). 22-минутная генерация выглядела как
мёртвая тишина.

Фикс: root logger получает те же console/file handlers (INFO/DEBUG),
content_bot.propagate=False (без дублей), шумные библиотеки приглушены.

Запуск: python tests/test_bot_logging_root.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402  (поднимает логирование на импорте)


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    root = logging.getLogger()
    cb = logging.getLogger("content_bot")

    print("\n[root logger — есть handlers (модульные логи видимы)]")
    _assert(len(root.handlers) >= 2,
            f"у root ≥2 handlers (console+file), got {len(root.handlers)}", errors)
    kinds = {type(h).__name__ for h in root.handlers}
    _assert("StreamHandler" in kinds, f"console-handler на root, got {kinds}", errors)
    _assert(any("FileHandler" in k for k in kinds),
            f"file-handler на root, got {kinds}", errors)
    _assert(root.level <= logging.INFO,
            f"root level ≤ INFO (got {logging.getLevelName(root.level)})", errors)

    print("\n[content_bot — без дублей]")
    _assert(cb.propagate is False,
            "content_bot.propagate=False (иначе каждый лог дважды)", errors)
    _assert(len(cb.handlers) >= 2, "у content_bot остались свои handlers", errors)

    print("\n[модульный логгер реально доходит до root-handler]")
    probe = logging.getLogger("hyperframes_broll")
    rec: list = []
    class _Catch(logging.Handler):
        def emit(self, r): rec.append(r.getMessage())
    c = _Catch(level=logging.INFO)
    root.addHandler(c)
    try:
        probe.info("[hf_broll] probe-видимость")
    finally:
        root.removeHandler(c)
    _assert(rec == ["[hf_broll] probe-видимость"],
            f"INFO от hyperframes_broll доходит до root, got {rec}", errors)

    print("\n[шумные библиотеки приглушены]")
    for noisy in ("httpx", "telegram", "httpcore", "urllib3"):
        lvl = logging.getLogger(noisy).level
        _assert(lvl >= logging.WARNING,
                f"{noisy} ≥ WARNING (got {logging.getLevelName(lvl)})", errors)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
