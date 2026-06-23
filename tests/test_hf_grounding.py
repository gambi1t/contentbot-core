"""Анти-галлюцинация вторичного текста сцен (Артём 23.06, GPT-5 §11).

Сборщик HF-сцены видел только ОДНУ сцену → для multi-element архетипов (checklist,
path_map) выдумывал пункты/стадии, которых нет в сценарии (реальный баг scene_02:
«Контент-конвейер / Авто-монтаж вертикалей / Пайплайн дистрибуции» — автор такого
не говорил). Фикс: _scene_grounding_block даёт сборщику ВЕСЬ сценарий + факт-
дисциплину (фабрикация запрещена, перефраз разрешён).

Run: python tests/test_hf_grounding.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as hf  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


_SB = {"scenes": [
    {"id": "scene_01", "script_beat": "Пропал почти на два месяца"},
    {"id": "scene_02", "business_archetype": "checklist", "script_beat": "За это время собрал несколько рабочих штук"},
    {"id": "scene_03", "script_beat": "Голосового ассистента которая ведёт дела за меня"},
    {"id": "scene_04", "script_beat": "Контент-завод который делает ролики практически сам"},
    {"id": "scene_05", "script_beat": "Ещё пару клиентов попросили рабочие инструменты"},
]}


def test_includes_full_script(errors):
    print("\n-- блок содержит ВЕСЬ сценарий (все beat'ы) --")
    b = hf._scene_grounding_block(_SB)
    for frag in ("два месяца", "несколько рабочих штук", "Голосового ассистента",
                 "Контент-завод", "клиентов попросили рабочие инструменты"):
        _assert(frag in b, f"в блоке есть фрагмент «{frag}»", errors)


def test_has_fact_discipline(errors):
    print("\n-- жёсткая факт-дисциплина (запрет фабрикации) --")
    bl = hf._scene_grounding_block(_SB).lower()
    _assert("не выдумывай" in bl, "явный запрет «не выдумывай»", errors)
    _assert("факт-дисциплина" in bl or "галлюцинац" in bl, "помечено как анти-галлюцинация", errors)
    _assert("пункт" in bl and ("checklist" in bl or "чек-лист" in bl), "правило про пункты списков/checklist", errors)


def test_allows_paraphrase(errors):
    print("\n-- перефраз/обобщение РАЗРЕШены (не запрещаем всё) --")
    bl = hf._scene_grounding_block(_SB).lower()
    _assert("перефраз" in bl and "разреш" in bl, "перефраз явно разрешён", errors)
    _assert("голосовой ассистент" in bl, "пример допустимого обобщения present", errors)


def test_empty_storyboard(errors):
    print("\n-- пустой storyboard → не падает --")
    b = hf._scene_grounding_block({})
    _assert(isinstance(b, str) and "не выдумывай" in b.lower(), "пустой → блок-правило без краша", errors)


def main():
    print("=" * 60 + "\nHF scene grounding (anti-hallucination)\n" + "=" * 60)
    errors = []
    for fn in (test_includes_full_script, test_has_fact_discipline,
               test_allows_paraphrase, test_empty_storyboard):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all grounding tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
