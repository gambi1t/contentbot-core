"""TDD: комбайн B-roll — нарезка влога по планам (scene-detection), 17 июня.

Чистая логика (без сети/ffmpeg):
- _parse_scene_timestamps(ffmpeg_stderr): вытащить pts_time из showinfo.
- _segments_from_scenes(scene_ts, duration, min_len, max_len): из таймкодов
  смены кадра построить сегменты (start, len) — ОДИН клип на план, от начала
  плана, длиной min(план, max_len); планы короче min_len выкинуть.

Интеграция (download/ffmpeg-cut/tag) — отдельно, через сервер.

Запуск: python tests/test_harvest_broll.py
"""
from __future__ import annotations
import os, sys
from pathlib import Path
os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
sys.path.insert(0, str(Path(__file__).parent.parent))

import harvest_broll as H  # noqa: E402


def _a(cond, msg, errs):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        errs.append(msg)


def test_parse(errs):
    print("\n[_parse_scene_timestamps]")
    stderr = (
        "[Parsed_showinfo_1 @ 0x55] n:0 pts:30720 pts_time:12.8 duration:1\n"
        "ffmpeg noise line without timestamp\n"
        "[Parsed_showinfo_1 @ 0x55] n:1 pts:72000 pts_time:30 duration:1\n"
        "[Parsed_showinfo_1 @ 0x55] n:2 pts:108000 pts_time:45.25 duration:1\n"
    )
    r = H._parse_scene_timestamps(stderr)
    _a(r == [12.8, 30.0, 45.25], f"вытащил pts_time по порядку (got {r})", errs)
    _a(H._parse_scene_timestamps("") == [], "пустой stderr → []", errs)


def test_segments(errs):
    print("\n[_segments_from_scenes — план→сегмент, ОДИН клип/план]")
    # planы: 0-10,10-20,20-30,30-35 → cap 6, последний 5s
    r = H._segments_from_scenes([10, 20, 30], 35.0, min_len=1.5, max_len=6.0)
    _a(r == [(0.0, 6.0), (10.0, 6.0), (20.0, 6.0), (30.0, 5.0)],
       f"равномерные планы + cap (got {r})", errs)

    print("\n[короткий план (<min_len) выкидывается]")
    # 0-2(ok), 2-2.5(0.5 drop), 2.5-10(7.5→cap6), 10-12(2 ok)
    r = H._segments_from_scenes([2, 2.5, 10], 12.0, min_len=1.5, max_len=6.0)
    _a(r == [(0.0, 2.0), (2.5, 6.0), (10.0, 2.0)],
       f"план 0.5с выкинут (got {r})", errs)

    print("\n[длинный план обрезается до max_len]")
    r = H._segments_from_scenes([], 20.0, min_len=1.5, max_len=6.0)
    _a(r == [(0.0, 6.0)], f"нет смен → весь ролик, обрезан до 6 (got {r})", errs)

    print("\n[нет планов и ролик короче min_len → пусто]")
    _a(H._segments_from_scenes([], 1.0, 1.5, 6.0) == [], "1с ролик → []", errs)

    print("\n[вход неотсортирован/с дублями → нормализуется]")
    r = H._segments_from_scenes([20, 10, 10, 30], 35.0, 1.5, 6.0)
    _a(r == [(0.0, 6.0), (10.0, 6.0), (20.0, 6.0), (30.0, 5.0)],
       f"сорт+дедуп (got {r})", errs)

    print("\n[таймкоды за пределами duration игнорируются]")
    r = H._segments_from_scenes([10, 100], 35.0, 1.5, 6.0)
    _a(r == [(0.0, 6.0), (10.0, 6.0)], f">duration отброшен (got {r})", errs)

    print("\n[пустой вход и нулевая длительность безопасны]")
    _a(H._segments_from_scenes([], 0.0, 1.5, 6.0) == [], "0с → []", errs)


def main():
    e = []
    test_parse(e)
    test_segments(e)
    print()
    if e:
        print(f"❌ FAIL — {len(e)}:"); [print("   -", x) for x in e]; return 1
    print("✅ ALL PASS"); return 0


if __name__ == "__main__":
    sys.exit(main())
