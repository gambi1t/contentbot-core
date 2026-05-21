"""Сборка pro-монтажа ролика #2 «Дорогие пустые часы».

Формат, одобренный Артёмом: хук аватаром → весь центр 50/50 → аватар в конце.
8 сегментов, каждый split ≤4.05с (длина клипа вставки).

Сценарий (озвучка avatar_01.mp4, ~32.4с):
  [HOOK]  Самые дорогие часы в моём бизнесе — когда трасса пустая.
          В выходные очередь. Во вторник днём — два человека.
          Но аренда, зарплаты и свет идут все семь дней.
          Пустой будний день — это минус.
          Я считал не то — не выручку выходных, а загрузку за неделю.
          Занялся буднями: корпоративы, дневной тариф, автошколы.
  [CTA]   Как — в канале «Юмсунов про реальный бизнес».

B-roll (broll_01..06):
  0 WeekLoad    — загрузка по дням: будни пустые
  1 Costs7      — расходы идут все 7 дней
  2 EmptyDay    — пустой будний день = минус
  3 WrongMetric — считал не ту метрику
  4 FillBudni   — занялся буднями
  5 WeekFull    — расписание закрылось

Запуск НА СЕРВЕРЕ:  python _assemble_pro_montage2.py
Выход: _montage_test2/final_pro.mp4
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from video_assembler import assemble_auto_montage

PROJ = Path(__file__).parent / "_montage_test2"

# хук аватаром → 6 split-вставок подряд → аватар на CTA
MONTAGE_PLAN = [
    {"start": 0.00,  "end": 4.22,  "layout": "avatar_full", "broll_index": None},
    {"start": 4.22,  "end": 8.22,  "layout": "split",       "broll_index": 0},  # WeekLoad
    {"start": 8.22,  "end": 12.22, "layout": "split",       "broll_index": 1},  # Costs7
    {"start": 12.22, "end": 16.22, "layout": "split",       "broll_index": 2},  # EmptyDay
    {"start": 16.22, "end": 20.22, "layout": "split",       "broll_index": 3},  # WrongMetric
    {"start": 20.22, "end": 24.22, "layout": "split",       "broll_index": 4},  # FillBudni
    {"start": 24.22, "end": 28.22, "layout": "split",       "broll_index": 5},  # WeekFull
    {"start": 28.22, "end": 32.44, "layout": "avatar_full", "broll_index": None},
]


def main() -> int:
    if not (PROJ / "avatar_01.mp4").exists():
        print("FAIL: нет avatar_01.mp4")
        return 1
    brolls = sorted(PROJ.glob("broll_*.mp4"))
    print(f"B-roll вставок: {len(brolls)}")
    if len(brolls) < 6:
        print("FAIL: нужно 6 B-roll вставок")
        return 1

    out = assemble_auto_montage(
        PROJ, layout="pro", montage_plan=MONTAGE_PLAN,
        subtitles=False, brand_name="default",
    )
    final = PROJ / "final_pro.mp4"
    shutil.move(str(out), str(final))
    print(f"OK pro: {final} — {final.stat().st_size // 1024} KB")
    print(f"Сегментов: {len(MONTAGE_PLAN)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
