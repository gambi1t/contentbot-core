"""Unit tests for telethon_uploader #selfie intake (path B) — ядро.

Большое selfie-видео (>20 МБ) не качается Bot API (лимит 20 МБ). Путь B: владелец
шлёт оригинал ДОКУМЕНТОМ в Saved Messages с тегом #selfie, telethon_uploader
(MTProto, до 2 ГБ) скачивает в selfie-inbox и прописывает путь в pending.json,
откуда бот подхватывает на «✅ Обработать видео».

Закрепляем чистую логику:
- _find_active_selfie(pending) — uid в state 'selfie_waiting_video', НЕ 'upload_final_video' (#crosspost).
- SELFIE_TAG == "#selfie" ≠ TRIGGER_TAG.
- _selfie_target_path(uid) — стабильный .mp4 + .part-сосед.

telethon мокаем на случай отсутствия в dev. Сеть/скачивание — E2E на сервере.
Run: python tests/test_selfie_intake.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")

# telethon может отсутствовать в dev — мокаем ДО импорта, чтобы module-level
# client = TelegramClient(...) и декораторы не упали.
for _m in ("telethon", "telethon.events", "telethon.tl", "telethon.tl.types"):
    sys.modules.setdefault(_m, MagicMock())

sys.path.insert(0, str(Path(__file__).parent.parent))

import telethon_uploader as tu  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def test_find_basic(errors):
    print("\n-- find: single user waiting --")
    r = tu._find_active_selfie({"123": {"state": "selfie_waiting_video"}})
    _assert(r is not None and r[0] == "123", "uid 123 found", errors)


def test_find_none_other_state(errors):
    print("\n-- find: unrelated state → None --")
    _assert(tu._find_active_selfie({"123": {"state": "selfie_text_review"}}) is None, "→ None", errors)


def test_find_not_crosspost(errors):
    print("\n-- find: upload_final_video (#crosspost) NOT matched --")
    _assert(tu._find_active_selfie({"123": {"state": "upload_final_video"}}) is None,
            "crosspost not matched as selfie", errors)


def test_find_picks_right_among_many(errors):
    print("\n-- find: picks selfie waiter among mixed --")
    r = tu._find_active_selfie({
        "1": {"state": "idle"}, "2": {"state": "upload_final_video"},
        "3": {"state": "selfie_waiting_video"}, "4": {"state": "selfie_music_picking"}})
    _assert(r is not None and r[0] == "3", f"picks uid 3 (got {r[0] if r else None})", errors)


def test_find_empty(errors):
    print("\n-- find: empty → None --")
    _assert(tu._find_active_selfie({}) is None, "empty → None", errors)


def test_find_missing_state_key(errors):
    print("\n-- find: entry без 'state' tolerated --")
    r = tu._find_active_selfie({"1": {"foo": "bar"}, "2": {"state": "selfie_waiting_video"}})
    _assert(r is not None and r[0] == "2", "skips malformed, finds waiter", errors)


def test_find_owner_match(errors):
    print("\n-- find: owner_id matches owner's waiting entry --")
    r = tu._find_active_selfie({"384671843": {"state": "selfie_waiting_video"}}, owner_id=384671843)
    _assert(r is not None and r[0] == "384671843", "owner matched", errors)


def test_find_owner_ignores_stranger(errors):
    print("\n-- find: owner_id ignores OTHER waiter (тест-аккаунт pollution) --")
    # Критичный кейс бага: тест-аккаунт 6730055130 завис в selfie_waiting_video,
    # владелец 384671843 НЕ ждёт → должен вернуть None, а НЕ перехватить чужого.
    r = tu._find_active_selfie(
        {"6730055130": {"state": "selfie_waiting_video"},
         "384671843": {"state": "idle"}}, owner_id=384671843)
    _assert(r is None, "stranger not picked when owner not waiting", errors)


def test_find_owner_among_many(errors):
    print("\n-- find: owner picked even if stranger also waiting --")
    r = tu._find_active_selfie(
        {"6730055130": {"state": "selfie_waiting_video"},
         "384671843": {"state": "selfie_waiting_video"}}, owner_id=384671843)
    _assert(r is not None and r[0] == "384671843", "owner over stranger", errors)


def test_selfie_tag_value(errors):
    print("\n-- tag: SELFIE_TAG == '#selfie' --")
    _assert(tu.SELFIE_TAG == "#selfie", f"got {tu.SELFIE_TAG!r}", errors)


def test_selfie_tag_distinct(errors):
    print("\n-- tag: #selfie != #crosspost --")
    _assert(tu.SELFIE_TAG != tu.TRIGGER_TAG, "distinct from crosspost", errors)


def test_target_path_stable(errors):
    print("\n-- target: stable .mp4 per uid --")
    p = tu._selfie_target_path("123")
    _assert(str(p).endswith(".mp4") and "123" in p.name, f"{p.name}", errors)
    _assert(tu._selfie_target_path("123") == p, "deterministic", errors)


def test_target_part_sibling(errors):
    print("\n-- target: .part sibling --")
    p = tu._selfie_target_path("123")
    part = p.with_suffix(p.suffix + ".part")
    _assert(part.name == p.name + ".part", f"{part.name}", errors)


def main() -> int:
    print("=" * 60 + "\ntelethon_uploader: #selfie intake (path B)\n" + "=" * 60)
    errors: list[str] = []
    for fn in (test_find_basic, test_find_none_other_state, test_find_not_crosspost,
               test_find_picks_right_among_many, test_find_empty, test_find_missing_state_key,
               test_find_owner_match, test_find_owner_ignores_stranger, test_find_owner_among_many,
               test_selfie_tag_value, test_selfie_tag_distinct,
               test_target_path_stable, test_target_part_sibling):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all selfie intake tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
