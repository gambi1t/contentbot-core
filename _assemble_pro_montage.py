"""Сборка PRO-монтажа: Claude проанализировал сценарий + 6 B-roll-вставок
и составил montage_plan со сменой раскладок (avatar_full / split / broll_full).

Тот же движок, что в content-bot-2 (`_assemble_pro` / layout="pro"), но
montage_plan задаётся напрямую — план составлен с учётом смысла каждой
вставки, а не по имени файла.

Сценарий (озвучка avatar_01.mp4, 32.12с):
  1. [HOOK]  Я уволил себя из роли надзирателя. Контроль стал качественнее,
            а у меня появилось время на то, чем должен заниматься собственник.
  2.        Подключил ИИ-ассистента: он слушает планёрки через Плауд,
            сам ставит задачи в Битрикс.
  3.        Я ничего не записываю и не догоняю людей в чатах.
  4.        Вечером — отчёт: что сделано, что висит, кто провалил.
  5.        Раньше операционка сидела в голове. Теперь — в системе.
            Голова свободна для решений.
  6. [CTA]  Как собрал — в Telegram-канале «Юмсунов про реальный бизнес».

B-roll (broll_01..06):
  0 broll_01 Chaos    — хаос непрочитанных уведомлений
  1 broll_02 Plaud    — диктофон слушает планёрку
  2 broll_03 TaskFly  — задачи летят сотрудникам
  3 broll_04 Bitrix   — задачи наполняют систему
  4 broll_05 Report   — вечерний отчёт
  5 broll_06 Freed    — голова свободна для решений

Запуск НА СЕРВЕРЕ:  python _assemble_pro_montage.py
Выход: _montage_test/final_pro.mp4
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from video_assembler import assemble_auto_montage

PROJ = Path(__file__).parent / "_montage_test"

# ── Монтажный план: хук аватаром → весь центр 50/50 → аватар в конце ──
# 8 сегментов. Аватар говорит хук, дальше всё тело ролика идёт в split
# 50/50 (аватар внизу, B-roll меняется сверху каждые ~4с), финал — снова
# аватар на CTA. Каждый split ≤4.05с (длина клипа вставки).
MONTAGE_PLAN = [
    {"start": 0.00,  "end": 4.00,  "layout": "avatar_full", "broll_index": None},
    {"start": 4.00,  "end": 8.02,  "layout": "split",       "broll_index": 0},  # Chaos
    {"start": 8.02,  "end": 12.04, "layout": "split",       "broll_index": 1},  # Plaud
    {"start": 12.04, "end": 16.06, "layout": "split",       "broll_index": 3},  # Bitrix
    {"start": 16.06, "end": 20.06, "layout": "split",       "broll_index": 2},  # TaskFly
    {"start": 20.06, "end": 24.08, "layout": "split",       "broll_index": 4},  # Report
    {"start": 24.08, "end": 28.10, "layout": "split",       "broll_index": 5},  # Freed
    {"start": 28.10, "end": 32.12, "layout": "avatar_full", "broll_index": None},
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
