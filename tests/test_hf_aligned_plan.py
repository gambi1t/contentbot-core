"""B-roll sync P0 — content-aligned планировщик + валидатор плана.

Проверяем: вставки стоят на таймкоде своих beat'ов (синхрон), broll_index=порядок
сцены, нормализация длины (min/max), нет пересечений, непрерывное покрытие [0,D],
слой scene_01/cta=broll_full, частичный фолбэк (низкий matched_ratio → None),
валидатор ловит битый план.

Run: python tests/test_hf_aligned_plan.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_alignment as al  # noqa: E402
import video_assembler as va  # noqa: E402

_TRANSCRIPT = (
    "Всем привет Пропал почти на два месяца Сейчас покажу на что я их потратил "
    "За это время собрал несколько рабочих штук Голосового ассистента которая "
    "ведет дела за меня Контент завод который делает ролики практически сам "
    "Ещё пару клиентов попросили рабочие инструменты которые я тоже реализовал "
    "Подпишись чтобы не пропустить"
)


def _mk_words(text, dt=0.4):
    toks = text.split()
    return [{"word": w, "start": round(i * dt, 3), "end": round(i * dt + dt * 0.9, 3)}
            for i, w in enumerate(toks)]


_WORDS = _mk_words(_TRANSCRIPT)
_D = round(len(_TRANSCRIPT.split()) * 0.4, 3)
_SCENES = [
    {"id": "scene_01", "business_archetype": "hero_number", "script_beat": "Пропал почти на два месяца"},
    {"id": "scene_02", "business_archetype": "checklist", "script_beat": "собрал несколько рабочих штук"},
    {"id": "scene_03", "business_archetype": "stack_layers", "script_beat": "Голосового ассистента которая ведет дела"},
    {"id": "scene_04", "business_archetype": "path_map", "script_beat": "Контент завод который делает ролики"},
    {"id": "scene_05", "business_archetype": "table_snapshot", "script_beat": "пару клиентов попросили рабочие инструменты"},
    {"id": "scene_06", "business_archetype": "final_cta", "script_beat": "Подпишись чтобы не пропустить"},
]


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_plan_valid_and_contiguous(errors):
    print("\n-- план валиден + непрерывен [0,D] --")
    plan, rep = va.build_content_aligned_hf_plan(_D, _SCENES, _WORDS)
    _assert(plan is not None, f"план построен (decision={rep.get('decision')})", errors)
    verr = va.validate_montage_plan(plan, _D, n_broll=len(_SCENES))
    _assert(verr == [], f"валидатор без нарушений: {verr}", errors)
    _assert(abs(plan[0]["start"]) < 0.02 and abs(plan[-1]["end"] - _D) < 0.05,
            "покрытие 0..D", errors)


def test_inserts_on_their_beat(errors):
    print("\n-- каждая вставка стоит на таймкоде своего beat'а (СИНХРОН) --")
    plan, rep = va.build_content_aligned_hf_plan(_D, _SCENES, _WORDS)
    by_bi = {s["broll_index"]: s for s in plan if s.get("broll_index") is not None}
    al_res = al.align_storyboard_to_words(_SCENES, _WORDS)
    ok_all = True
    for idx, t in enumerate(al_res["timings"]):
        if t["status"] not in ("aligned", "low_confidence"):
            continue
        seg = by_bi.get(idx)
        if not seg:
            continue
        center = (t["time_start"] + t["time_end"]) / 2
        # вставка должна пересекаться с окном beat'а (±1с на нормализацию/сдвиг)
        covers = seg["start"] - 1.0 <= center <= seg["end"] + 1.0
        ok_all = ok_all and covers
        if not covers:
            print(f"     scene idx{idx}: beat_center={center:.1f} вне сегмента [{seg['start']},{seg['end']}]")
    _assert(ok_all, "все сматченные вставки покрывают свой beat-центр", errors)


def test_broll_index_is_scene_order(errors):
    print("\n-- broll_index = порядковый индекс сцены --")
    plan, _ = va.build_content_aligned_hf_plan(_D, _SCENES, _WORDS)
    bis = [s["broll_index"] for s in plan if s.get("broll_index") is not None]
    _assert(bis == sorted(bis), f"индексы по возрастанию во времени: {bis}", errors)
    _assert(all(0 <= b < len(_SCENES) for b in bis), "индексы в диапазоне сцен", errors)


def test_layout_policy(errors):
    print("\n-- scene_01 (хук) и final_cta → broll_full, остальное split --")
    plan, _ = va.build_content_aligned_hf_plan(_D, _SCENES, _WORDS)
    by_bi = {s["broll_index"]: s for s in plan if s.get("broll_index") is not None}
    if 0 in by_bi:
        _assert(by_bi[0]["layout"] == "broll_full", "scene_01 → broll_full", errors)
    if 5 in by_bi:
        _assert(by_bi[5]["layout"] == "broll_full", "final_cta → broll_full", errors)
    mids = [by_bi[i]["layout"] for i in (1, 2, 3, 4) if i in by_bi]
    _assert(all(l == "split" for l in mids), f"средние → split ({mids})", errors)


def test_no_overlap_min_gap(errors):
    print("\n-- нет пересечений вставок (валидатор уже это ловит, доп. проверка зазора) --")
    plan, _ = va.build_content_aligned_hf_plan(_D, _SCENES, _WORDS)
    segs = [s for s in plan if s.get("broll_index") is not None]
    ok = all(segs[i]["end"] <= segs[i + 1]["start"] + 0.001 for i in range(len(segs) - 1))
    _assert(ok, "B-roll сегменты не пересекаются", errors)


def test_duration_normalization(errors):
    print("\n-- длина вставки в [min,max] --")
    plan, _ = va.build_content_aligned_hf_plan(_D, _SCENES, _WORDS)
    durs = [round(s["end"] - s["start"], 2) for s in plan if s.get("broll_index") is not None]
    _assert(all(d <= 5.2 + 0.01 for d in durs), f"≤ max_insert 5.2 ({durs})", errors)
    _assert(all(d >= 1.0 for d in durs), f"≥ 1с (после разрешения пересечений) ({durs})", errors)


def test_low_ratio_fallback(errors):
    print("\n-- низкий matched_ratio → None (caller откатится на bookend) --")
    foreign = [{"id": f"scene_0{i}", "business_archetype": "checklist",
                "script_beat": "картинг глэмпинг трасса гонки руль"} for i in range(1, 6)]
    plan, rep = va.build_content_aligned_hf_plan(_D, foreign, _WORDS)
    _assert(plan is None and rep["decision"] == "fallback_bookend",
            f"чужие beat'ы → fallback (ratio={rep.get('matched_ratio')})", errors)


def test_validator_catches_bad(errors):
    print("\n-- validate_montage_plan ловит дыры/перехлёст/выход за диапазон --")
    gap = [{"start": 0.0, "end": 3.0, "layout": "avatar_full", "broll_index": None},
           {"start": 5.0, "end": 10.0, "layout": "split", "broll_index": 0}]  # дыра 3..5
    _assert(va.validate_montage_plan(gap, 10.0, 1), "ловит разрыв", errors)
    overlap = [{"start": 0.0, "end": 6.0, "layout": "avatar_full", "broll_index": None},
               {"start": 4.0, "end": 10.0, "layout": "split", "broll_index": 0}]
    _assert(va.validate_montage_plan(overlap, 10.0, 1), "ловит перехлёст", errors)
    noidx = [{"start": 0.0, "end": 10.0, "layout": "split", "broll_index": None}]
    _assert(va.validate_montage_plan(noidx, 10.0, 1), "ловит split без индекса", errors)
    good = [{"start": 0.0, "end": 4.0, "layout": "avatar_full", "broll_index": None},
            {"start": 4.0, "end": 8.0, "layout": "split", "broll_index": 0},
            {"start": 8.0, "end": 10.0, "layout": "avatar_full", "broll_index": None}]
    _assert(va.validate_montage_plan(good, 10.0, 1) == [], "валидный план → []", errors)


def main():
    print("=" * 60 + "\nContent-aligned HF montage plan (B-roll sync P0)\n" + "=" * 60)
    errors = []
    for fn in (test_plan_valid_and_contiguous, test_inserts_on_their_beat,
               test_broll_index_is_scene_order, test_layout_policy, test_no_overlap_min_gap,
               test_duration_normalization, test_low_ratio_fallback, test_validator_catches_bad):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all aligned-plan tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
