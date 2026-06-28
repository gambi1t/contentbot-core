"""TDD: «все точки» B-roll загрузки — реордер «Готовых материалов» (Fix1),
селфи+B-roll >20МБ #broll (Fix2), библиотека >20МБ→#lib (Fix3).

Запуск: python -m pytest tests/test_broll_all_places.py -v
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


# ── Fix1: реордер «Готовых материалов» (перенумерование broll_NN.mp4) ──────

def test_video_list_numeric_sort(tmp_path):
    (tmp_path / "broll_02.mp4").write_text("b")
    (tmp_path / "broll_01.mp4").write_text("a")
    (tmp_path / "broll_10.mp4").write_text("j")
    names = [p.name for p in bot._broll_ready_video_list(tmp_path)]
    assert names == ["broll_01.mp4", "broll_02.mp4", "broll_10.mp4"], "не числовая сортировка"


def test_renumber_applies_desired_order(tmp_path):
    (tmp_path / "broll_01.mp4").write_text("A", encoding="utf-8")
    (tmp_path / "broll_02.mp4").write_text("B", encoding="utf-8")
    (tmp_path / "broll_03.mp4").write_text("C", encoding="utf-8")
    vids = bot._broll_ready_video_list(tmp_path)  # [01=A, 02=B, 03=C]
    desired = [vids[2], vids[0], vids[1]]          # хотим C, A, B
    bot._renumber_broll_videos(tmp_path, desired)
    assert (tmp_path / "broll_01.mp4").read_text(encoding="utf-8") == "C"
    assert (tmp_path / "broll_02.mp4").read_text(encoding="utf-8") == "A"
    assert (tmp_path / "broll_03.mp4").read_text(encoding="utf-8") == "B"
    # ровно 3 файла, без хвостов tmp
    assert len(list(tmp_path.glob("broll_*.mp4"))) == 3
    assert not list(tmp_path.glob("broll_tmp_*"))


def test_ready_kb_shows_reorder_when_two_videos(tmp_path):
    (tmp_path / "broll_01.mp4").write_text("a")
    (tmp_path / "broll_02.mp4").write_text("b")
    cbs = [b.callback_data for r in bot._broll_ready_kb(tmp_path).inline_keyboard for b in r]
    assert "broll_ready_reorder" in cbs


def test_ready_kb_hides_reorder_for_one_video(tmp_path):
    (tmp_path / "broll_01.mp4").write_text("a")
    cbs = [b.callback_data for r in bot._broll_ready_kb(tmp_path).inline_keyboard for b in r]
    assert "broll_ready_reorder" not in cbs, "реордер не нужен при 1 клипе"


def test_bot_has_reorder_callbacks():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert 'query.data == "broll_ready_reorder"' in src
    assert 'query.data.startswith("broll_ready_move:")' in src


# ── Fix2: селфи+B-roll >20МБ через #broll ─────────────────────────────────

def test_telethon_serves_selfie_broll_state():
    src = (ROOT / "telethon_uploader.py").read_text(encoding="utf-8")
    assert "selfie_broll_uploading_video" in src, "#broll не обслуживает селфи-флоу"
    assert "broll_ready_material" in src and "broll2_uploading" in src, "потеряны прочие флоу"


def test_selfie_handlers_pickbroll_and_precheck():
    src = (ROOT / "selfie" / "handlers.py").read_text(encoding="utf-8")
    assert "_ingest_selfie_broll_inbox" in src, "нет приёмки #broll в селфи-пикер"
    assert 'action == "pickbroll"' in src, "нет колбэка забора"
    assert "20 * 1024 * 1024" in src, "нет пре-чека >20МБ в видео-хендлере"


# ── Fix3: библиотека >20МБ → редирект на #lib ─────────────────────────────

def test_lib_admin_20mb_redirect_to_lib():
    src = (ROOT / "library_manager.py").read_text(encoding="utf-8")
    assert "20 * 1024 * 1024" in src, "нет пре-чека >20МБ в lib_admin"
    assert "#lib" in src, "нет редиректа на #lib-маршрут"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
