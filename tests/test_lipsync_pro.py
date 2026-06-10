"""Тест защиты lip-sync в _assemble_pro (10 июня).

Баг со встречи с Максимом («2 бизнеса, семья и только одни сутки», Про-монтаж
8 сегментов): губы уезжают от звука «на каком-то этапе». Замер по факту:
аудио финала НЕ дрейфует (−21.5мс константа во всех окнах), а ВИДЕО потеряло
2 кадра (845 vs 847 у аватара) → весь контент после потери играет раньше звука.

Механизм: границы плана — произвольные float (не кратны кадру 1/30с) →
каждый сегмент режется НЕЗАВИСИМО (-ss сдвигает контент до 1 кадра, -t
округляет длительность, -shortest дорезает видео по AAC-фрейму 21.3мс) →
ошибки складываются по сегментам.

Фикс (три слоя):
1. _quantize_plan_to_frames — снап границ плана к сетке кадров (1/FPS):
   контент и позиция каждого сегмента совпадают точно.
2. Сегменты кодируются ТОЛЬКО-ВИДЕО (-an, без -c:a, без -shortest) —
   нечему резать видео по аудио-границе.
3. Финальный мукс: конкат видео + ОДНО непрерывное аудио аватара
   (никакой склейки аудио из кусков).

Запуск: python tests/test_lipsync_pro.py
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import video_assembler as va  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    FPS = va.FPS

    print("\n[_quantize_plan_to_frames — снап к сетке кадров]")
    _assert(hasattr(va, "_quantize_plan_to_frames"),
            "есть функция _quantize_plan_to_frames", errors)
    if not hasattr(va, "_quantize_plan_to_frames"):
        print("\n❌ FAIL — нет функции, дальше не проверяем")
        return 1

    plan = [
        {"start": 0.0, "end": 3.37, "layout": "avatar_full", "broll_index": None},
        {"start": 3.37, "end": 7.123, "layout": "split", "broll_index": 0},
        {"start": 7.123, "end": 11.51, "layout": "broll_full", "broll_index": 1},
        {"start": 11.51, "end": 28.233, "layout": "avatar_full", "broll_index": None},
    ]
    q = va._quantize_plan_to_frames(plan)

    for seg in q:
        for key in ("start", "end"):
            t = seg[key]
            frames = t * FPS
            _assert(abs(frames - round(frames)) < 1e-6,
                    f"{seg['layout']} {key}={t:.4f} кратен кадру 1/{FPS}", errors)

    print("\n[стыки сегментов без дыр и нахлёстов]")
    for a, b in zip(q, q[1:]):
        _assert(abs(a["end"] - b["start"]) < 1e-9,
                f"стык {a['layout']}→{b['layout']}: end == start", errors)

    print("\n[границы диапазона и метаданные сохранены]")
    _assert(abs(q[0]["start"] - 0.0) < 1e-9, "первый start = 0", errors)
    _assert(q[1]["layout"] == "split" and q[1]["broll_index"] == 0,
            "layout/broll_index не потеряны", errors)
    _assert(all(s["end"] > s["start"] for s in q), "нет пустых сегментов", errors)

    # Сегмент короче полукадра после снапа — выбрасывается
    tiny = [{"start": 0.0, "end": 0.01, "layout": "avatar_full", "broll_index": None},
            {"start": 0.01, "end": 5.0, "layout": "avatar_full", "broll_index": None}]
    qt = va._quantize_plan_to_frames(tiny)
    _assert(len(qt) == 1 and abs(qt[0]["end"] - 5.0) < 1e-9,
            "нулевой после снапа сегмент выброшен", errors)

    print("\n[_assemble_pro — сегменты только-видео, без -shortest]")
    src = inspect.getsource(va._assemble_pro)
    _assert('"-shortest"' not in src,
            "-shortest убран (резал видео по AAC-границе)", errors)
    _assert('_quantize_plan_to_frames' in src,
            "_assemble_pro квантует план к кадрам", errors)
    # В сегментных энкодах не должно быть пер-сегментного AAC
    seg_zone = src[src.index("Build segments"):src.index("Concat all segments")]
    _assert('"-c:a"' not in seg_zone and '"aac"' not in seg_zone,
            "в сегментах НЕТ пер-сегментного аудио-энкода", errors)
    _assert('"-an"' in seg_zone, "сегменты кодируются с -an (только видео)", errors)

    print("\n[_assemble_pro — финал: конкат видео + ОДНО аудио аватара]")
    mux_zone = src[src.index("Concat all segments"):]
    _assert('"-map", "0:v"' in mux_zone and '"-map", "1:a"' in mux_zone,
            "финальный мукс: видео из конката + аудио из аватара", errors)
    _assert("avatar_full" in mux_zone,
            "источник аудио — непрерывный avatar_full", errors)

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
