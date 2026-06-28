"""TDD: загрузка B-roll >20МБ через #broll-телетон (Part B).

  * BROLL_TAG_RE (из telethon_uploader.py): «#broll N» → порядок N, «#broll» →
    None, «#brolly»/мусор → не матчится. Паттерн читаем из файла, telethon
    локально не импортируем (нет зависимости).
  * _ingest_broll_inbox: переносит клипы в draft ПО ПОРЯДКУ (поле order),
    возвращает число добавленных, кладёт в source_items в правильной
    последовательности.
  * клавиатура загрузки содержит кнопку «Забрать большие файлы».

Запуск: python -m pytest tests/test_broll_telethon_pickup.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import broll.handlers as bh  # noqa: E402


# ── BROLL_TAG_RE (вычитываем паттерн из файла, без импорта telethon) ──────

def _broll_re():
    src = (ROOT / "telethon_uploader.py").read_text(encoding="utf-8")
    m = re.search(r"BROLL_TAG_RE = re\.compile\(\s*r'([^']+)'", src)
    assert m, "BROLL_TAG_RE не найдена в telethon_uploader.py"
    return re.compile(m.group(1), re.IGNORECASE)


def test_broll_tag_with_number():
    pat = _broll_re()
    assert pat.match("#broll 2").group(1) == "2"
    assert pat.match("#broll 10").group(1) == "10"


def test_broll_tag_without_number():
    pat = _broll_re()
    m = pat.match("#broll")
    assert m is not None and m.group(1) is None


def test_broll_tag_rejects_junk():
    pat = _broll_re()
    assert pat.match("просто видео") is None
    assert pat.match("#brolly") is None, "слово-приклейка не должна триггерить"
    assert pat.match("#selfie") is None


# ── _ingest_broll_inbox: порядок по order ────────────────────────────────

class _It:
    def __init__(self, kind, path):
        self.kind, self.path = kind, path


class _Draft:
    def __init__(self, work_dir):
        self.work_dir = str(work_dir)
        self.source_items = []


def test_ingest_orders_by_order_field(tmp_path, monkeypatch):
    # validate_upload_media требует ffprobe — мокаем «всё ок»
    monkeypatch.setattr(bh, "validate_upload_media", lambda p, k: (True, ""))
    inbox_dir = tmp_path / "inbox"; inbox_dir.mkdir()
    # три «клипа» приехали в перемешанном порядке доставки, но с order 1/2/3
    files = {}
    for o in (3, 1, 2):
        f = inbox_dir / f"broll_x_{o}.mp4"; f.write_bytes(b"x")
        files[o] = f
    inbox = [
        {"path": str(files[3]), "order": 3},
        {"path": str(files[1]), "order": 1},
        {"path": str(files[2]), "order": 2},
    ]
    work = tmp_path / "work"
    draft = _Draft(work)
    added = bh._ingest_broll_inbox(draft, inbox)
    assert added == 3
    # source_items должны идти по order 1→2→3
    names = [Path(it.path).name for it in draft.source_items]
    assert names == ["up_001.mp4", "up_002.mp4", "up_003.mp4"]
    # и файлы реально перенесены в work_dir
    for it in draft.source_items:
        assert Path(it.path).exists() and Path(it.path).parent == work


def test_ingest_empty_returns_zero():
    draft = _Draft("/nonexistent")
    assert bh._ingest_broll_inbox(draft, []) == 0


def test_ingest_skips_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "validate_upload_media", lambda p, k: (True, ""))
    inbox = [{"path": str(tmp_path / "ghost.mp4"), "order": 1}]
    draft = _Draft(tmp_path / "work")
    assert bh._ingest_broll_inbox(draft, inbox) == 0


def test_upload_keyboard_has_pickup():
    kb = bh._broll_upload_keyboard()
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "b2up_pickbroll" in cbs
    assert "b2up_done" in cbs and "b2up_cancel" in cbs


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
