"""TDD: 2 бага B-roll Pipeline 2, найденные на canary panferov (25 июня).

БАГ 1 (музыка глушит голос): broll/assembler.py клал музыку статичным volume=0.18
(-15dB) БЕЗ дакинга → голос и музыка в одном диапазоне. Эталон (music_mixer.py,
селфи): -18dB + sidechaincompress (музыка притихает под речь). Берём эталонное.

БАГ 2 (обложки чужого бренда): cover-библиотека (selfie.cover) бренд-слепая —
читала глобальный _LIBRARY_DIR → panferov видел обложки бренда shoes. Должна
резолвиться per-brand через _avatars_dir_for_brand (default→assets/avatars Артёма,
shoes→.../shoes). Чиним обе точки вызова (broll b2cov + selfie cover) через DI pool_dir.

Запуск: python tests/test_broll_canary_fixes.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from broll import assembler as bro_asm  # noqa: E402
from selfie import cover as selfie_cover  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


# ── БАГ 1: музыка / дакинг ────────────────────────────────────────────────────

def test_music_volume_and_ducking(errors):
    print("\n-- broll музыка: -18dB + sidechaincompress (как эталон music_mixer) --")
    _assert(bro_asm.MUSIC_VOLUME < 0.15,
            f"MUSIC_VOLUME приглушён (было 0.18; got {bro_asm.MUSIC_VOLUME})", errors)
    src = Path(bro_asm.__file__).read_text(encoding="utf-8")
    _assert("sidechaincompress" in src,
            "в финальном фильтре добавлен sidechaincompress (музыка притихает под голос)", errors)


# ── БАГ 2: cover-библиотека per-brand ─────────────────────────────────────────

def test_cover_pool_dir_overrides_global(errors):
    print("\n-- list_library_sample(pool_dir=...) сканит именно ЭТУ папку, не глобал --")
    with tempfile.TemporaryDirectory() as td:
        for name in ("01_artem.jpg", "02_artem.jpg"):
            (Path(td) / name).write_bytes(b"x" * 1200)
        sample = selfie_cover.list_library_sample(6, None, pool_dir=Path(td))
        ids = {s["id"] for s in sample}
        _assert(ids == {"01_artem", "02_artem"}, f"вернул фото из pool_dir (got {ids})", errors)


def test_scan_library_pool_dir(errors):
    print("\n-- _scan_library(pool_dir) читает переданную папку --")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "x.png").write_bytes(b"x" * 1200)
        rows = selfie_cover._scan_library(pool_dir=Path(td))
        _assert(len(rows) == 1 and rows[0]["id"] == "x", f"скан pool_dir (got {rows})", errors)


def test_broll_cover_dispatch_passes_brand(errors):
    print("\n-- bot.py b2cov + handle_broll_cover_cb прокидывают бренд-резолвер --")
    bot_src = (Path(selfie_cover.__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    bh_src = (Path(selfie_cover.__file__).parent.parent / "broll" / "handlers.py").read_text(encoding="utf-8")
    _assert("cover_pool_dir=_avatars_dir_for_brand" in bot_src.replace(" ", "") or
            "cover_pool_dir=_avatars_dir_for_brand(" in bot_src,
            "bot.py b2cov передаёт cover_pool_dir=_avatars_dir_for_brand(...)", errors)
    _assert("cover_pool_dir" in bh_src and "pool_dir=cover_pool_dir" in bh_src.replace(" ", "") or
            "pool_dir=cover_pool_dir" in bh_src,
            "handle_broll_cover_cb прокидывает pool_dir в list_library_sample", errors)


def test_selfie_cover_passes_pool_dir(errors):
    print("\n-- selfie cover (все места) тоже бренд-aware --")
    sh_src = (Path(selfie_cover.__file__).parent / "handlers.py").read_text(encoding="utf-8")
    _assert("_COVER_POOL_DIR_FN" in sh_src or "pool_dir=" in sh_src,
            "selfie cover-превью передаёт pool_dir (бренд-резолвер из init)", errors)


def main() -> int:
    errors: list = []
    for fn in (test_music_volume_and_ducking, test_cover_pool_dir_overrides_global,
               test_scan_library_pool_dir, test_broll_cover_dispatch_passes_brand,
               test_selfie_cover_passes_pool_dir):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK all canary-fix tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
