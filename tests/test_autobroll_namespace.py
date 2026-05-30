"""Tests for namespace separation: AutoBroll (AI-вставки) vs реальные broll.

W1 (27 May 2026): раньше AutoBroll писал в `projects/<id>/broll_NN.mp4` —
тот же namespace что и SMM-загрузки через «📥 Готовые материалы». При
сборке `_find_broll` брал ВСЁ broll_*.mp4 в кучу — AI-визуалы перемешивались
с реальными кадрами.

Теперь:
- AutoBroll пишет в `projects/<id>/autobroll/auto_NN.mp4`
- `broll_NN.mp4` ТОЛЬКО для реальных клипов (SMM/YouTube/прочее)
- `_find_broll(proj, mode='real'|'ai'|'mix')` — выбор источника

Стиль: без pytest, main() → 0/1.
Запуск: python tests/test_autobroll_namespace.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    if not cond:
        errors.append(f"FAIL {safe}")
        print(f"  FAIL {safe}")
    else:
        print(f"  OK {safe}")


def _make_video_stub(path: Path) -> None:
    """Минимальный MP4-стаб (>1KB чтобы пройти size-фильтры)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1500)


# ─── _find_broll mode-параметр ────────────────────────────────────────────

def test_find_broll_mode_real(errors: list[str]) -> None:
    print("\n-- _find_broll(mode='real') → только broll_*.mp4 --")
    import video_assembler as va
    tmp = Path(tempfile.mkdtemp(prefix="proj_real_"))
    try:
        _make_video_stub(tmp / "broll_01.mp4")
        _make_video_stub(tmp / "broll_02.mp4")
        _make_video_stub(tmp / "autobroll" / "auto_01.mp4")
        _make_video_stub(tmp / "autobroll" / "auto_02.mp4")
        result = va._find_broll(tmp, mode="real")
        names = sorted(p.name for p in result)
        _assert(
            names == ["broll_01.mp4", "broll_02.mp4"],
            f"real → broll_*.mp4 only ({names})",
            errors,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_broll_mode_ai(errors: list[str]) -> None:
    print("\n-- _find_broll(mode='ai') → только autobroll/auto_*.mp4 --")
    import video_assembler as va
    tmp = Path(tempfile.mkdtemp(prefix="proj_ai_"))
    try:
        _make_video_stub(tmp / "broll_01.mp4")
        _make_video_stub(tmp / "autobroll" / "auto_01.mp4")
        _make_video_stub(tmp / "autobroll" / "auto_02.mp4")
        _make_video_stub(tmp / "autobroll" / "auto_03.mp4")
        result = va._find_broll(tmp, mode="ai")
        names = sorted(p.name for p in result)
        _assert(
            names == ["auto_01.mp4", "auto_02.mp4", "auto_03.mp4"],
            f"ai → auto_*.mp4 only ({names})",
            errors,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_broll_mode_mix(errors: list[str]) -> None:
    print("\n-- _find_broll(mode='mix') → оба источника --")
    import video_assembler as va
    tmp = Path(tempfile.mkdtemp(prefix="proj_mix_"))
    try:
        _make_video_stub(tmp / "broll_01.mp4")
        _make_video_stub(tmp / "broll_02.mp4")
        _make_video_stub(tmp / "autobroll" / "auto_01.mp4")
        result = va._find_broll(tmp, mode="mix")
        names = sorted(p.name for p in result)
        _assert(
            names == ["auto_01.mp4", "broll_01.mp4", "broll_02.mp4"],
            f"mix → both sources ({names})",
            errors,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_broll_default_mode_is_mix(errors: list[str]) -> None:
    """Backward-compat: _find_broll(proj) без mode = mix."""
    print("\n-- _find_broll(proj) без mode → mix (backward-compat) --")
    import video_assembler as va
    tmp = Path(tempfile.mkdtemp(prefix="proj_dflt_"))
    try:
        _make_video_stub(tmp / "broll_01.mp4")
        _make_video_stub(tmp / "autobroll" / "auto_01.mp4")
        result = va._find_broll(tmp)
        names = sorted(p.name for p in result)
        _assert(
            "broll_01.mp4" in names and "auto_01.mp4" in names,
            f"default mode includes both ({names})",
            errors,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── auto_broll._render_all пишет в подпапку ──────────────────────────────

def test_render_target_path_in_autobroll_subdir(errors: list[str]) -> None:
    """auto_broll._render_all(out_dir) должна писать в out_dir/autobroll/auto_NN.mp4
    (не в out_dir/broll_NN.mp4 как раньше).

    Проверяем через статический анализ кода — без запуска Remotion.
    """
    print("\n-- auto_broll._render_all → out_dir/autobroll/auto_NN.mp4 --")
    src = (Path(__file__).parent.parent / "auto_broll.py").read_text(encoding="utf-8")
    # Старый паттерн должен быть удалён
    _assert(
        'out_dir / f"broll_{i:02d}.mp4"' not in src,
        "old broll_NN.mp4 pattern removed",
        errors,
    )
    # Новый паттерн должен присутствовать
    has_new = (
        '/ f"auto_{i:02d}.mp4"' in src
        or 'autobroll' in src.lower() and 'auto_' in src
    )
    _assert(has_new, "new auto_NN.mp4 pattern present in autobroll/", errors)


def test_card_autobroll_handler_does_not_delete_real_broll(errors: list[str]) -> None:
    """bot.py card_autobroll handler не должен удалять broll_*.mp4 (SMM-загрузки).
    Удалять только old autobroll/auto_*.mp4.
    """
    print("\n-- bot.py card_autobroll: НЕ удаляет broll_*.mp4 --")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    # Старая строка с массовым удалением broll_*.mp4 в card_autobroll handler.
    # Если она ещё на месте — fix не сделан.
    bad_pattern = 'for _old in proj_dir.glob("broll_*.mp4"):'
    _assert(
        bad_pattern not in src,
        f"old wipe-all pattern removed ({bad_pattern!r})",
        errors,
    )


# ─── runner ───────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print("AutoBroll namespace separation tests (W1)")
    print("=" * 60)
    errors: list[str] = []
    test_find_broll_mode_real(errors)
    test_find_broll_mode_ai(errors)
    test_find_broll_mode_mix(errors)
    test_find_broll_default_mode_is_mix(errors)
    test_render_target_path_in_autobroll_subdir(errors)
    test_card_autobroll_handler_does_not_delete_real_broll(errors)
    print("\n" + "=" * 60)
    if errors:
        print(f"Found {len(errors)} failure(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK all AutoBroll namespace tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
