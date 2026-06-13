"""Тест BrollItem + durable-draft Pipeline 2 (13 июня).

По синтезу CTO-ревью: in-memory draft рвётся на длинных ветках (HF 8-25 мин →
рестарт → всё пропало). Фаза 1 фундамент: BrollItem-контракт + durable-draft
с атомарной записью (паттерн bot_state.save_pending: tempfile + os.replace),
status/source_mode/items/ttl. Stale-callback и double-launch — через status,
без полного CAS (ужато под 1 клиента).

Запуск: python tests/test_broll_draft.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from broll.draft import (  # noqa: E402
    BrollItem, BrollDraft, Status, SourceMode,
    save_draft, load_draft, new_draft_id, cleanup_expired,
)


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    drafts_dir = Path(tempfile.mkdtemp(prefix="broll_drafts_"))

    print("\n[BrollItem — контракт + сериализация]")
    it = BrollItem(kind="image", origin="upload", path="/x/a.jpg", label="фото",
                   semantic_role="hook")
    d = it.to_dict()
    _assert(d["kind"] == "image" and d["origin"] == "upload", "to_dict ключевые поля", errors)
    it2 = BrollItem.from_dict(d)
    _assert(it2.path == "/x/a.jpg" and it2.semantic_role == "hook",
            "round-trip from_dict", errors)
    _assert(it2.safe_to_loop is True, "дефолт safe_to_loop", errors)
    try:
        BrollItem(kind="bogus", origin="upload", path="/x")
        _assert(False, "невалидный kind → ValueError", errors)
    except ValueError:
        _assert(True, "невалидный kind → ValueError", errors)

    print("\n[new_draft_id — уникальный, стабильный по входу]")
    a = new_draft_id(123, 1781000000.0)
    b = new_draft_id(123, 1781000001.0)
    _assert(a != b, "разное время → разные id", errors)
    _assert(a.startswith("broll_") and "123" in a, "id содержит префикс+user", errors)

    print("\n[save/load — атомарно, round-trip]")
    draft = BrollDraft(
        draft_id=new_draft_id(123, 1781000000.0), user_id=123, chat_id=456,
        status=Status.AWAITING_SOURCE, source_mode=None,
        script_text="закадровый текст", voice_estimate_sec=31.4,
        source_items=[it], work_dir="/tmp/w", created_at=1781000000.0,
        updated_at=1781000000.0, ttl_hours=24,
    )
    save_draft(draft, drafts_dir)
    p = drafts_dir / f"{draft.draft_id}.json"
    _assert(p.exists(), "файл черновика создан", errors)
    _assert(not list(drafts_dir.glob("*.tmp")), "временный .tmp убран (атомарность)", errors)
    loaded = load_draft(draft.draft_id, drafts_dir)
    _assert(loaded is not None and loaded.script_text == "закадровый текст",
            "загрузился сценарий", errors)
    _assert(len(loaded.source_items) == 1 and loaded.source_items[0].kind == "image",
            "items round-trip", errors)
    _assert(load_draft("broll_нет", drafts_dir) is None, "несуществующий → None", errors)

    print("\n[status / source_mode — валидные значения]")
    _assert(set([Status.AWAITING_SOURCE, Status.UPLOADING, Status.HF_RUNNING,
                 Status.PREVIEW_READY, Status.ASSEMBLING, Status.DONE,
                 Status.FAILED, Status.EXPIRED]) <= set(Status.ALL),
            "все статусы в Status.ALL", errors)
    _assert(set([SourceMode.AUTO, SourceMode.MANUAL, SourceMode.UPLOAD,
                 SourceMode.HF_ONLY, SourceMode.AUTO_HF]) == set(SourceMode.ALL),
            "5 режимов источника", errors)

    print("\n[мутация + повторное сохранение]")
    loaded.status = Status.PREVIEW_READY
    loaded.source_mode = SourceMode.AUTO
    loaded.touch(1781000500.0)
    save_draft(loaded, drafts_dir)
    again = load_draft(draft.draft_id, drafts_dir)
    _assert(again.status == Status.PREVIEW_READY and again.source_mode == "auto",
            "обновлённый статус/режим персистнулся", errors)
    _assert(again.updated_at == 1781000500.0, "touch обновил updated_at", errors)

    print("\n[TTL / is_expired + cleanup]")
    fresh = BrollDraft(draft_id=new_draft_id(9, 1781000000.0), user_id=9, chat_id=9,
                       status=Status.AWAITING_SOURCE, source_mode=None,
                       script_text="x", voice_estimate_sec=10, source_items=[],
                       work_dir="/tmp", created_at=1781000000.0,
                       updated_at=1781000000.0, ttl_hours=24)
    _assert(not fresh.is_expired(now=1781000000.0 + 3600), "в пределах TTL — жив", errors)
    _assert(fresh.is_expired(now=1781000000.0 + 25 * 3600), "за TTL — истёк", errors)
    save_draft(fresh, drafts_dir)
    n = cleanup_expired(drafts_dir, now=1781000000.0 + 25 * 3600)
    _assert(n >= 1, f"cleanup удалил истёкшие, got {n}", errors)
    _assert(load_draft(fresh.draft_id, drafts_dir) is None, "истёкший удалён с диска", errors)

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
