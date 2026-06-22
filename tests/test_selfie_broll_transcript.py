"""Regression: B-roll «из сценария» (gen / hf / aivideo) брал текст из
data['selfie_edited'] — а это BOOL-флаг (handlers.py:696), не текст. После
правки текста selfie_edited=True → `(True or ...).strip()` → AttributeError:
'bool' object has no attribute 'strip' → кнопки «🎨 Графика», «🎞 HyperFrames»,
«🎬 AI-видео» молча падали (Артём 22.06: «нажимаю HyperFrames — ничего»).

Источник истины для текста — selfie_transcript: правка его обновляет
(handlers.py:542), burn кладёт актуальный (handlers.py:695).

telegram/selfie-сабмодули мокаем (как в test_finished_video).
Run: python tests/test_selfie_broll_transcript.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

for _m in (
    "telegram", "telegram.ext",
    "selfie.broll_picker", "selfie.cover", "selfie.music",
    "selfie.edit", "selfie.transcribe",
):
    sys.modules.setdefault(_m, MagicMock())

sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie import handlers as sh  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def test_bool_selfie_edited_does_not_crash(errors):
    print("\n-- selfie_edited=True (bool) НЕ роняет, берём selfie_transcript --")
    # Точное воспроизведение бага: после правки текста selfie_edited=True (bool).
    data = {"selfie_edited": True, "selfie_transcript": "привет мир"}
    try:
        r = sh._broll_source_transcript(data)
        _assert(r == "привет мир", f"вернул транскрипт (got {r!r})", errors)
    except AttributeError as e:
        _assert(False, f"крашнулся как раньше: {e}", errors)


def test_uses_transcript_not_edited_flag(errors):
    print("\n-- источник = selfie_transcript (selfie_edited игнорируется) --")
    data = {"selfie_edited": False, "selfie_transcript": "  текст сценария  "}
    _assert(sh._broll_source_transcript(data) == "текст сценария", "trim + транскрипт", errors)


def test_empty_when_no_transcript(errors):
    print("\n-- нет транскрипта → '' (ветка покажет «нет текста», не упадёт) --")
    _assert(sh._broll_source_transcript({}) == "", "пусто → ''", errors)
    _assert(sh._broll_source_transcript({"selfie_edited": True}) == "", "bool без текста → ''", errors)


def main() -> int:
    print("=" * 60 + "\nselfie B-roll source transcript (gen/hf/aivideo regression)\n" + "=" * 60)
    errors: list = []
    for fn in (test_bool_selfie_edited_does_not_crash, test_uses_transcript_not_edited_flag,
               test_empty_when_no_transcript):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all broll-transcript tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
