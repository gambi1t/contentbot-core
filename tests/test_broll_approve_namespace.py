"""Phase A / Critical 1: namespace-фикс коллизии broll_approve.

Проблема: ранняя ветка handle_callback `query.data == "broll_approve"` (Pipeline 2,
bot.py ~13199) безусловна и срабатывает ДО легаси `effective_action == "broll_approve"`
(«Сохранить в Notion», bot.py ~18647) → затеняет легаси (баг для panferov).

Фикс (миграционно-безопасный):
- Pipeline 2 апрув → namespace `b2flow:approve:<draft_id>` (несёт draft_id, робастно к рестарту);
- старый `broll_approve` оставлен как back-compat shim С guard'ом `broll_draft` —
  если контекста Pipeline 2 нет, НЕ return → fall-through к легаси (легаси снова работает);
- legacy `effective_action == "broll_approve"` (18647) НЕ трогаем.

Запуск: python tests/test_broll_approve_namespace.py
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

from broll import handlers as bh  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(("  OK " if cond else "  FAIL ") + safe)
    if not cond:
        errors.append(safe)


def _callbacks(markup) -> list[str]:
    out = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                out.append(btn.callback_data)
    return out


def test_keyboard_namespaced_approve(errors: list[str]) -> None:
    print("\n-- _approval_keyboard(draft_id=...) → апрув в namespace b2flow:approve:<id> --")
    kb = bh._approval_keyboard(notion_url=None, draft_id="abc123")
    cbs = _callbacks(kb)
    _assert("b2flow:approve:abc123" in cbs, "апрув = b2flow:approve:abc123", errors)
    _assert("broll_approve" not in cbs, "голого broll_approve больше нет (с draft_id)", errors)


def test_keyboard_backcompat_without_draft_id(errors: list[str]) -> None:
    print("\n-- без draft_id → back-compat broll_approve (не ломаем старые вызовы) --")
    kb = bh._approval_keyboard(notion_url=None)
    cbs = _callbacks(kb)
    _assert("broll_approve" in cbs, "без draft_id → broll_approve (back-compat)", errors)


def test_bot_has_namespaced_branch_and_guard(errors: list[str]) -> None:
    print("\n-- bot.py: новая ветка b2flow:approve + guard на старом broll_approve --")
    src = (Path(bh.__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    _assert('b2flow:approve' in src, "есть ветка b2flow:approve в bot.py", errors)
    # старый broll_approve-перехват теперь guard'ится наличием broll_draft (Pipeline 2 контекст)
    idx = src.find('query.data == "broll_approve"')
    _assert(idx != -1, "ранняя ветка broll_approve на месте (как shim)", errors)
    if idx != -1:
        window = src[idx: idx + 400]
        _assert("broll_draft" in window,
                "ранний broll_approve guard'ится broll_draft (иначе fall-through к легаси)", errors)
    # легаси save-to-Notion не тронут
    _assert('effective_action == "broll_approve"' in src,
            "легаси effective_action broll_approve (save-to-Notion) на месте", errors)


def main() -> int:
    errors: list[str] = []
    for fn in (test_keyboard_namespaced_approve,
               test_keyboard_backcompat_without_draft_id,
               test_bot_has_namespaced_branch_and_guard):
        fn(errors)
    print("\n" + ("FAIL" if errors else "OK") + f" ({len(errors)} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
