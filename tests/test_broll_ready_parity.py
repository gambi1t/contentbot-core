"""TDD: паритет флоу «📥 Готовые материалы» (флоу B) — счётчик + >20МБ #broll.

  * _broll_ready_counts — считает видео broll_*.mp4 + фото в photos/;
  * _broll_ready_kb — счётчик материалов на кнопке Готово + кнопка забора #broll;
  * _ingest_broll_ready_inbox — кладёт #broll-клипы в проект как broll_NN.mp4
    СТРОГО по полю order (order=1 → broll_01, проверяем по содержимому);
  * telethon: state broll_ready_material входит в _BROLL_UPLOAD_STATES.

Запуск: python -m pytest tests/test_broll_ready_parity.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def test_broll_ready_counts(tmp_path):
    proj = tmp_path / "p"
    (proj / "photos").mkdir(parents=True)
    (proj / "broll_01.mp4").write_bytes(b"x")
    (proj / "broll_02.mp4").write_bytes(b"x")
    (proj / "photos" / "ready_01.jpg").write_bytes(b"x")
    v, p = bot._broll_ready_counts(proj)
    assert (v, p) == (2, 1)


def test_broll_ready_counts_empty(tmp_path):
    assert bot._broll_ready_counts(tmp_path / "nope") == (0, 0)


def test_broll_ready_kb_has_counter_and_pickup(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "broll_01.mp4").write_bytes(b"x")
    kb = bot._broll_ready_kb(proj)
    cbs = [b.callback_data for r in kb.inline_keyboard for b in r]
    labels = [b.text for r in kb.inline_keyboard for b in r]
    assert "broll_ready_pickbroll" in cbs, "нет кнопки забора больших файлов"
    assert "broll_ready_done" in cbs
    assert any("материал" in t for t in labels), "счётчик не показан на кнопке Готово"


def test_ingest_ready_orders_by_order_field(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    files = {}
    for o in (3, 1, 2):  # доставка вперемешку
        f = inbox_dir / f"b_{o}.mp4"
        f.write_text(f"clip{o}", encoding="utf-8")
        files[o] = f
    inbox = [
        {"path": str(files[3]), "order": 3},
        {"path": str(files[1]), "order": 1},
        {"path": str(files[2]), "order": 2},
    ]
    monkeypatch.setattr(bot, "_project_dir", lambda data: proj)
    monkeypatch.setattr(bot, "_reload_broll_inbox_disk", lambda uid: inbox)
    monkeypatch.setattr(bot, "_save_pending", lambda p: None)
    bot.pending.setdefault(999, {})
    added = bot._ingest_broll_ready_inbox(999, {})
    assert added == 3
    # order=1 → broll_01, order=2 → broll_02, order=3 → broll_03 (по содержимому)
    assert (proj / "broll_01.mp4").read_text(encoding="utf-8") == "clip1"
    assert (proj / "broll_02.mp4").read_text(encoding="utf-8") == "clip2"
    assert (proj / "broll_03.mp4").read_text(encoding="utf-8") == "clip3"


def test_ingest_ready_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "_project_dir", lambda data: tmp_path / "proj")
    monkeypatch.setattr(bot, "_reload_broll_inbox_disk", lambda uid: [])
    assert bot._ingest_broll_ready_inbox(999, {}) == 0


def test_telethon_states_include_ready_material():
    src = (ROOT / "telethon_uploader.py").read_text(encoding="utf-8")
    assert "_BROLL_UPLOAD_STATES" in src, "константа множества состояний не заведена"
    assert "broll_ready_material" in src, "#broll не обслуживает флоу B"
    assert "broll2_uploading" in src, "#broll потерял флоу A"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
