"""stale-state: selfie_hf_scenes (раскадровка для content-aligned sync) живёт
ВНУТРИ одного ролика (gen→montage), но после успешной сборки сбрасывается, иначе
carry_session_keys протащит её в финал → при повторном заходе в пикер всплывёт
СТАРАЯ раскадровка (Артём 23.06; риск введён вместе с sync в этом же сеансе).

Run: python tests/test_hf_scenes_reset.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie import handlers as H  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_persistent_membership(errors):
    print("\n-- selfie_hf_scenes персистентен (нужен sync внутри ролика) --")
    _assert("selfie_hf_scenes" in H.PERSISTENT_SELFIE_KEYS,
            "selfie_hf_scenes в PERSISTENT_SELFIE_KEYS", errors)


def test_carry_within_reel(errors):
    print("\n-- carry_session_keys протаскивает раскадровку, пока ролик не собран --")
    old = {"selfie_hf_scenes": [{"id": "scene_01"}], "selfie_words": [{"word": "a"}]}
    new = {"state": "selfie_waiting_title"}
    H.carry_session_keys(old, new)
    _assert(new.get("selfie_hf_scenes") == [{"id": "scene_01"}],
            "раскадровка перенесена в новый pending (внутри ролика)", errors)


def test_cleared_stays_cleared(errors):
    print("\n-- после сброса (как post-assembly) carry НЕ воскрешает раскадровку --")
    # имитируем состояние ПОСЛЕ сборки: selfie_hf_scenes уже popнут
    old = {"selfie_words": [{"word": "a"}]}  # без selfie_hf_scenes
    new = {"state": "selfie_waiting_title"}
    H.carry_session_keys(old, new)
    _assert("selfie_hf_scenes" not in new,
            "очищенная раскадровка не возвращается carry'ем → нет stale при перезаходе", errors)


def test_pop_is_safe_when_absent(errors):
    print("\n-- pop selfie_hf_scenes идемпотентен (нет ключа → не падает) --")
    d = {"state": "x"}
    d.pop("selfie_hf_scenes", None)  # как в assembly success path
    _assert("selfie_hf_scenes" not in d, "pop без ключа безопасен", errors)


def main():
    print("=" * 60 + "\nselfie_hf_scenes reset (stale-state)\n" + "=" * 60)
    errors = []
    for fn in (test_persistent_membership, test_carry_within_reel,
               test_cleared_stays_cleared, test_pop_is_safe_when_absent):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all hf-scenes-reset tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
