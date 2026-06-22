"""TDD for Phase 2 узел B — AI-video (Seedance) as a Pipeline-2 source.

Pure parts unit-tested: the fullscreen clip-plan (count+cost for the confirm
screen), the menu button, and parse_source_cb accepting the confirm/back
pseudo-modes (so the b2src router reaches them WITHOUT touching bot.py). The
async handler dispatch is Telethon-verified.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_fullscreen_plan_count_and_cost():
    import ai_video_broll as av
    p = av.fullscreen_plan(" ".join(["w"] * 90), clip_len=10)   # 90 words ≈ 36s
    assert p["n_clips"] == 4                                    # ceil(36/10), без +1 буфера (kling-switch 20.06)
    assert p["clip_len"] == 10
    assert p["est_sec"] == pytest.approx(36.0)
    assert p["cost"] == pytest.approx(4 * 10 * av.KLING_PRICE_PER_SEC_USD)   # 4 клипа × 10с × Kling $0.112/с


pytest.importorskip("telegram")


def test_source_menu_shows_aivideo_when_enabled():
    from broll.source_menu import source_menu_keyboard
    from broll.draft import SourceMode
    kb = source_menu_keyboard("d1", enabled_modes=[SourceMode.AI_VIDEO])
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "b2src:ai_video:d1" in cbs


def test_parse_accepts_aivideo_pseudomodes():
    from broll.source_menu import parse_source_cb
    assert parse_source_cb("b2src:ai_video:d1") == ("ai_video", "d1")
    assert parse_source_cb("b2src:ai_video_go:d1") == ("ai_video_go", "d1")
    assert parse_source_cb("b2src:ai_video_menu:d1") == ("ai_video_menu", "d1")


# ── #3: добор недостающих AI-клипов (partial-failure recovery) ────────────────
def test_aivideo_fill_wired_in_bot():
    src = (Path(__file__).resolve().parent.parent / "bot.py").read_text(encoding="utf-8")
    assert 'startswith("b2avfill:")' in src               # dispatch есть
    assert "handle_ai_video_fill" in src                   # зовёт хендлер
    assert '"b2av": "ai_video"' in src                     # гейт зонтиком b2av → ai_video (платный fal)


def test_handle_ai_video_fill_rebuilds_from_all_clips(monkeypatch, tmp_path):
    import types
    import asyncio
    import ai_video_broll
    import broll.handlers as bh

    work = tmp_path / "broll_runs" / "d1"
    clips_dir = work / ai_video_broll.CLIPS_SUBDIR
    clips_dir.mkdir(parents=True)
    (clips_dir / "ai_01.mp4").write_bytes(b"x")            # 1 клип уже есть

    def fake_regen(out_dir, indices=None, progress_cb=None):
        (Path(out_dir) / ai_video_broll.CLIPS_SUBDIR / "ai_02.mp4").write_bytes(b"y")
        return [Path(out_dir) / ai_video_broll.CLIPS_SUBDIR / "ai_02.mp4"], 1.12
    monkeypatch.setattr(ai_video_broll, "regen_ai_clips", fake_regen)

    fake_draft = types.SimpleNamespace(
        chat_id=42, work_dir=str(work), source_items=[],
        script_text="s", theme="t", notion_url=None, notion_page_id=None,
        source_mode="ai_video", touch=lambda *a: None)
    monkeypatch.setattr(bh, "load_draft", lambda did, d: fake_draft)
    monkeypatch.setattr(bh, "save_draft", lambda *a, **k: None)
    monkeypatch.setattr(bh, "materialize_items", lambda *a, **k: None)
    preview = {"called": False}

    async def fake_preview(*a, **k):
        preview["called"] = True
    monkeypatch.setattr(bh, "_send_hf_preview", fake_preview)

    class _Msg:
        async def edit_text(self, *a, **k): pass
        async def delete(self): pass

    class _Bot:
        async def send_message(self, *a, **k): return _Msg()

    ctx = types.SimpleNamespace(bot=_Bot())
    upd = types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=42))

    asyncio.run(bh.handle_ai_video_fill(upd, ctx, "d1", chat_id=42))
    assert len(fake_draft.source_items) == 2               # пересобрано из ai_01 + добранного ai_02
    assert preview["called"] is True                       # превью пере-показано


# ── #2 Инкремент 1: пер-сценный ре-ролл AI-видео ──────────────────────────────
def test_av_scene_picker_keyboards():
    from broll.handlers import _av_scene_picker_kb, _av_scene_action_kb, _approval_keyboard
    pick = [b.callback_data for row in _av_scene_picker_kb("d1", 3).inline_keyboard for b in row]
    assert "b2avsc:d1:1" in pick and "b2avsc:d1:3" in pick and "b2avback:d1" in pick
    act = [b.callback_data for row in _av_scene_action_kb("d1", 2).inline_keyboard for b in row]
    assert "b2avgo:d1:2" in act and "b2avre:d1" in act
    appr = [b.callback_data for row in _approval_keyboard(None, av_draft_id="d1").inline_keyboard
            for b in row if b.callback_data]
    assert "b2avre:d1" in appr                 # кнопка ре-ролла на превью AI-видео


def test_av_regen_go_rerolls_and_repreviews(monkeypatch, tmp_path):
    import types
    import asyncio
    import ai_video_broll
    import broll.handlers as bh

    work = tmp_path / "broll_runs" / "d1"
    clips_dir = work / ai_video_broll.CLIPS_SUBDIR
    clips_dir.mkdir(parents=True)
    (clips_dir / "ai_01.mp4").write_bytes(b"old")
    (clips_dir / "ai_02.mp4").write_bytes(b"x")

    called = {}

    def fake_regen(out_dir, indices=None, progress_cb=None):
        called["indices"] = indices
        (Path(out_dir) / ai_video_broll.CLIPS_SUBDIR / "ai_01.mp4").write_bytes(b"new")
        return [Path(out_dir) / ai_video_broll.CLIPS_SUBDIR / "ai_01.mp4"], 1.12
    monkeypatch.setattr(ai_video_broll, "regen_ai_clips", fake_regen)

    fake_draft = types.SimpleNamespace(
        chat_id=42, work_dir=str(work), source_items=[object(), object()],
        script_text="s", theme="t", notion_url=None, notion_page_id=None,
        source_mode="ai_video", touch=lambda *a: None)
    monkeypatch.setattr(bh, "load_draft", lambda did, d: fake_draft)
    monkeypatch.setattr(bh, "save_draft", lambda *a, **k: None)
    monkeypatch.setattr(bh, "materialize_items", lambda *a, **k: None)
    preview = {"n": 0}

    async def fake_preview(*a, **k):
        preview["n"] += 1
    monkeypatch.setattr(bh, "_send_hf_preview", fake_preview)

    class _Msg:
        async def edit_text(self, *a, **k): pass
        async def delete(self): pass

    class _Bot:
        async def send_message(self, *a, **k): return _Msg()

    upd = types.SimpleNamespace(
        callback_query=types.SimpleNamespace(message=types.SimpleNamespace(chat_id=42)))
    ctx = types.SimpleNamespace(bot=_Bot())

    asyncio.run(bh.handle_av_regen_go(upd, ctx, "d1", 1))
    assert called["indices"] == [1]             # ре-ролл именно сцены 1
    assert len(fake_draft.source_items) == 2    # пересобрано из всех ai_*.mp4
    assert preview["n"] == 1                     # превью пере-показано


def test_av_reroll_wired_in_bot():
    src = (Path(__file__).resolve().parent.parent / "bot.py").read_text(encoding="utf-8")
    for cb in ('"b2avre:"', '"b2avsc:"', '"b2avgo:"', '"b2avback:"'):
        assert f"startswith({cb})" in src, f"нет dispatch {cb}"
    assert '"b2av": "ai_video"' in src          # зонтик-гейт по ai_video
    for h in ("handle_av_regen_menu", "handle_av_regen_scene",
              "handle_av_regen_go", "handle_av_regen_back"):
        assert h in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
