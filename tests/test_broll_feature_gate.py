"""Phase A / High 1: единый feature-gate Pipeline 2.

Проблема: в _CALLBACK_FEATURE_MAP были только часть b2*-префиксов (b2man/b2scr/
b2src/b2hf/b2mus/b2cov), а поздние стадии (b2vc голос, b2vop превью, b2up загрузка,
b2flow апрув, b2title) НЕ гейтились → старая кнопка после выключения фичи / callback
во время раскатки проходил. + вход idea_fork:broll не гейтился (idea_pipeline:broll
гейтится).

Фикс: зонтичный "b2" -> broll_pipeline (ловит ВСЕ поздние b2*); специфичные
b2src:ai_video / b2av -> ai_video оставлены (двойной гейт money-leak). + гейт
idea_fork:broll.

Запуск: python tests/test_broll_feature_gate.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402
import tenant as _tenant  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(("  OK " if cond else "  FAIL ") + safe)
    if not cond:
        errors.append(safe)


_OFF = {"features": {"broll_pipeline": False, "ai_video": True, "hyperframes": True}}
_ON = {"features": {"broll_pipeline": True, "ai_video": True, "hyperframes": True}}
_AIVID_OFF = {"features": {"broll_pipeline": True, "ai_video": False}}

MAP = bot._CALLBACK_FEATURE_MAP


def _blocked(cb, tenant):
    return _tenant.callback_feature_blocked(cb, tenant, MAP)


def test_umbrella_catches_late_stages(errors: list[str]) -> None:
    print("\n-- зонтик b2: поздние стадии гейтятся при broll_pipeline OFF --")
    for cb in ("b2vc:ai", "b2vc:own", "b2vop:accept", "b2up_done",
               "b2flow:approve:d1", "b2title:gen"):
        _assert(_blocked(cb, _OFF) == "broll_pipeline",
                f"{cb} → broll_pipeline (OFF)", errors)


def test_double_gate_aivideo_still_specific(errors: list[str]) -> None:
    print("\n-- b2src:ai_video при broll_pipeline ON, ai_video OFF → ai_video --")
    _assert(_blocked("b2src:ai_video", _AIVID_OFF) == "ai_video",
            "Seedance/Kling money-leak гейт жив", errors)


def test_not_blocked_when_on(errors: list[str]) -> None:
    print("\n-- всё ON → b2* не блокируется --")
    for cb in ("b2vc:ai", "b2flow:approve:d1", "b2src:upload"):
        _assert(_blocked(cb, _ON) is None, f"{cb} не блокируется (ON)", errors)


def test_idea_fork_broll_gated(errors: list[str]) -> None:
    print("\n-- вход idea_fork:broll гейтится broll_pipeline (source) --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    idx = src.find('if query.data.startswith("idea_fork:")')
    _assert(idx != -1, "ветка idea_fork найдена", errors)
    if idx == -1:
        return
    window = src[idx: idx + 1400]
    _assert('feature_blocked' in window and 'broll_pipeline' in window,
            "broll-ветка idea_fork проверяет feature_blocked(broll_pipeline)", errors)


def main() -> int:
    errors: list[str] = []
    for fn in (test_umbrella_catches_late_stages, test_double_gate_aivideo_still_specific,
               test_not_blocked_when_on, test_idea_fork_broll_gated):
        fn(errors)
    print("\n" + ("FAIL" if errors else "OK") + f" ({len(errors)} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
