"""Тест мульти-удаления библиотеки B-roll (13 июня).

Артём: при удалении бот показывает 6 видео, кнопки 🗑 N, но удаляется по
одному. Нужна «удалить все показанные» (загрузил 6, все не нравятся — снёс
разом). Ядро: _delete_many по списку id; меню несёт кнопку; показанные id
персистятся (delall удаляет ровно показанный набор).

Запуск: python tests/test_library_multidelete.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import library_manager as lm  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[_delete_many — удаляет список, считает успехи/провалы]")
    deleted = []
    orig = lm.delete_library_file
    def fake_del(kind, item_id):
        if item_id == "bad":
            return None  # не найден
        deleted.append((kind, item_id))
        return f"{item_id}.mp4"
    lm.delete_library_file = fake_del
    try:
        ok, fail = lm._delete_many("video", ["a", "b", "bad", "c"])
    finally:
        lm.delete_library_file = orig
    _assert(ok == 3 and fail == 1, f"3 удалено, 1 провал (got ok={ok} fail={fail})", errors)
    _assert(deleted == [("video", "a"), ("video", "b"), ("video", "c")],
            "удалены именно валидные id по порядку", errors)
    _assert(lm._delete_many("video", []) == (0, 0), "пустой список → (0,0)", errors)

    print("\n[_del_browse_kb — кнопка «удалить все показанные (N)»]")
    samples = [{"id": f"id{i}"} for i in range(6)]
    kb = lm._del_browse_kb(samples, "video", "personal")
    flat = [b for row in kb.inline_keyboard for b in row]
    cbs = [b.callback_data for b in flat]
    texts = " ".join(b.text for b in flat)
    _assert(any("delall" in c for c in cbs), "есть кнопка delall", errors)
    _assert("6" in texts and ("все" in texts.lower() or "всё" in texts.lower()),
            "лейбл показывает число и «все»", errors)
    _assert(sum(1 for c in cbs if "delpick" in c) == 6, "одиночные 🗑 N сохранены", errors)

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
