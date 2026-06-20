"""TDD: pending_migrator — безопасный перенос pending при cutover (I3/R1/G5).

ChatGPT-ревью I3: переносить по ALLOWLIST (только известное безопасное), не
«удалять мусор». Цель — при пересадке на сервер core НЕ занести эфемерное
состояние Артёма: C:\\Temp-пути активной selfie-сессии (на другом хосте их нет),
активный `state` (зависнет на полпути). Оставить только персистентные указатели
карточек, чтобы /cards и «продолжить последнюю» работали.

Чистая функция migrate_pending (логика) — под TDD; CLI-обёртка (raw.json →
migrated.json + diff) — тонкая, проверяется запуском.

Запуск: python tests/test_pending_migrator.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import pending_migrator as pm  # noqa: E402

_errs: list[str] = []


def _assert(cond, msg):
    print(f"  {'OK' if cond else 'X FAIL'} {msg}")
    if not cond:
        _errs.append(msg)


def test_keeps_allowlist_keys():
    print("\n-- переносит безопасные ключи карточки --")
    raw = {"384671843": {
        "notion_page_id": "33b0ef6e-x", "card_data": {"title": "Летняя акция"},
        "card_brand": "shoes", "script": "текст сценария", "notion_edit_card": "33b0ef6e-x",
    }}
    migrated, dropped = pm.migrate_pending(raw)
    u = migrated["384671843"]
    _assert(u.get("notion_page_id") == "33b0ef6e-x", "notion_page_id сохранён")
    _assert(u.get("card_data", {}).get("title") == "Летняя акция", "card_data сохранён")
    _assert(u.get("card_brand") == "shoes", "card_brand сохранён")
    _assert(u.get("script") == "текст сценария", "script сохранён")
    _assert(u.get("notion_edit_card") == "33b0ef6e-x", "notion_edit_card сохранён")


def test_drops_ephemeral_keys():
    print("\n-- выкидывает эфемерные (C:/Temp пути, активный state) --")
    raw = {"384671843": {
        "notion_page_id": "x", "state": "selfie_music_picking",
        "selfie_tmp_dir": "C:/Temp/selfie_abc", "selfie_source": "C:/Temp/v.mp4",
        "selfie_subtitled": "C:/Temp/s.mp4", "stats_draft": {"x": 1},
        "voice_parts": [1, 2], "shotlist": ["a"],
    }}
    migrated, dropped = pm.migrate_pending(raw)
    u = migrated["384671843"]
    _assert("state" not in u, "активный state выкинут (не зависнет на хосте)")
    _assert("selfie_tmp_dir" not in u, "selfie_tmp_dir (C:/Temp) выкинут")
    _assert("selfie_source" not in u, "selfie_source выкинут")
    _assert("stats_draft" not in u, "stats_draft выкинут")
    _assert("voice_parts" not in u, "voice_parts выкинут")
    _assert(u.get("notion_page_id") == "x", "полезный notion_page_id остался")
    _assert("selfie_tmp_dir" in dropped.get("384671843", []), "выкинутое попало в diff")


def test_drops_empty_user():
    print("\n-- юзер без полезных ключей → выкинут целиком --")
    raw = {
        "111": {"state": "done", "selfie_tmp_dir": "C:/Temp/x"},  # только эфемерное
        "384671843": {"notion_page_id": "keep"},                  # есть полезное
    }
    migrated, dropped = pm.migrate_pending(raw)
    _assert("111" not in migrated, "юзер с только эфемерным → выкинут")
    _assert("384671843" in migrated, "юзер с полезным → оставлен")


def test_does_not_mutate_input():
    print("\n-- вход НЕ мутируется --")
    raw = {"1": {"notion_page_id": "x", "state": "active", "selfie_tmp_dir": "C:/t"}}
    pm.migrate_pending(raw)
    _assert(raw["1"].get("state") == "active", "исходный raw не тронут (state на месте)")
    _assert("selfie_tmp_dir" in raw["1"], "исходный raw не тронут (selfie_tmp_dir на месте)")


def test_int_and_str_keys():
    print("\n-- user_id ключи (int или str) сохраняются как есть --")
    raw = {384671843: {"notion_page_id": "x"}}  # int-ключ (как _load_pending кастует)
    migrated, _ = pm.migrate_pending(raw)
    _assert(384671843 in migrated, "int-ключ юзера сохранён")


def test_drops_new_seedance_pipeline2_cover_fields():
    """I6 (ChatGPT-ревью cutover): новые state-поля, появившиеся ПОСЛЕ написания
    allowlist (Seedance/ai_video, Pipeline-2 broll2_*, cover-gate, narrative,
    draft_id'ы) — ЭФЕМЕРНЫЕ, должны отбрасываться (whitelist), персистентные
    указатели карточки — сохраняться. Замок: если кто-то по ошибке внесёт
    активный draft_id/state в allowlist — этот тест упадёт.
    """
    print("\n-- I6: новые Seedance/Pipeline-2/cover поля отбрасываются --")
    raw = {"384671843": {
        # персистентное — должно остаться
        "notion_page_id": "keep-x", "card_data": {"title": "Тест"}, "card_brand": "shoes",
        "script": "сценарий", "idea": "идея",
        # новые эфемерные (Seedance / Pipeline-2 / cover-gate / narrative) — должны уйти
        "state": "broll2_uploading",
        "ai_video": True, "narrative": True,
        "broll2_draft_id": "d1", "broll2_title_text": "хук", "broll2_ownvoice": True,
        "broll2_cover_text": "t", "broll2_manual": {}, "broll2_edit_script": "s",
        "broll_draft_id": "d2", "broll_clips": ["c1"], "broll_lib_category": "apps",
        "draft_id": "d3", "cover_draft_id": "c", "cover_path": "C:/Temp/cov.jpg",
        "cover_options": [1], "cover_text": "txt", "description_draft": "dd",
        "carousel_seed": 7, "ideas_batch": [1, 2],
    }}
    migrated, dropped = pm.migrate_pending(raw)
    u = migrated["384671843"]
    # персистентное осталось
    _assert(u.get("notion_page_id") == "keep-x", "notion_page_id остался")
    _assert(u.get("card_data", {}).get("title") == "Тест", "card_data остался")
    _assert(u.get("script") == "сценарий" and u.get("idea") == "идея", "script/idea остались")
    # все новые эфемерные ушли
    for k in ("state", "ai_video", "narrative", "broll2_draft_id", "broll2_title_text",
              "broll2_ownvoice", "broll2_cover_text", "broll2_manual", "broll2_edit_script",
              "broll_draft_id", "broll_clips", "broll_lib_category", "draft_id",
              "cover_draft_id", "cover_path", "cover_options", "cover_text",
              "description_draft", "carousel_seed", "ideas_batch"):
        _assert(k not in u, f"{k} отброшен (эфемерное, не просочилось на ядро)")
    # абсолютный путь cover (C:/Temp на чужом хосте) точно в diff
    _assert("cover_path" in dropped.get("384671843", []), "cover_path (C:/Temp) попал в diff")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"\n{'='*60}\nRunning {len(tests)} pending_migrator tests\n{'='*60}")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _errs.append(f"{fn.__name__}: {e}")
            print(f"  X EXC {fn.__name__}: {e}")
    print(f"\n{'='*60}")
    print("ALL PASS" if not _errs else f"FAIL ({len(_errs)}): " + "; ".join(_errs))
    sys.exit(0 if not _errs else 1)
