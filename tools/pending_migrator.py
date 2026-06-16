"""pending_migrator — безопасный перенос pending.json при Phase 3 cutover.

ChatGPT-ревью I3 (+ R1/G5): переносить по ALLOWLIST, не «удалять мусор».
При пересадке на сервер core нельзя занести эфемерное состояние Артёма —
абсолютные C:\\Temp-пути активной selfie-сессии (на другом хосте их нет) и
активный `state` (бот зависнет на полпути флоу). Оставляем только
персистентные указатели карточек, чтобы /cards и «продолжить последнюю»
работали.

    python -m tools.pending_migrator --in pending.raw.json --out pending.migrated.json

Чистая функция migrate_pending — под TDD. CLI — тонкая обвязка (read/write/diff).
"""
from __future__ import annotations

import sys

# Только эти ключи переносятся (персистентные указатели карточки + сценарий).
# Всё остальное (state, selfie_tmp_dir/source/subtitled/cover/final, selfie_music_*,
# selfie_cover_*, stats_draft, voice_parts, shotlist, search_queries/videos,
# cover_pool_*) — эфемерное состояние сессии, на новом хосте бесполезно/вредно.
ALLOWED_PENDING_KEYS = frozenset({
    "notion_page_id",
    "notion_edit_card",
    "notion_edit_title",
    "notion_url",
    "card_data",
    "card_brand",
    "script",
    "idea",
})


def migrate_pending(raw: dict, allowed: frozenset = ALLOWED_PENDING_KEYS) -> tuple[dict, dict]:
    """Отфильтровать pending по allowlist. Вход НЕ мутируется.

    Возвращает (migrated, dropped):
      migrated — {user_id: {только allowed-ключи}}, юзеры без единого
        allowed-ключа выкинуты целиком (несли только эфемерное).
      dropped  — {user_id: [выброшенные ключи]} для diff-отчёта.
    """
    migrated: dict = {}
    dropped: dict = {}
    for uid, data in raw.items():
        if not isinstance(data, dict):
            dropped[uid] = ["<не dict — выкинут целиком>"]
            continue
        kept = {k: v for k, v in data.items() if k in allowed}
        drop = [k for k in data if k not in allowed]
        if kept:
            migrated[uid] = kept
        if drop:
            dropped[uid] = sorted(drop)
    return migrated, dropped


def _cli() -> int:
    import argparse
    import json
    from pathlib import Path

    p = argparse.ArgumentParser(prog="pending_migrator", description="Migrate pending.json by allowlist for cutover")
    p.add_argument("--in", dest="inp", required=True, help="pending.raw.json (снимок до миграции)")
    p.add_argument("--out", dest="outp", required=True, help="pending.migrated.json (результат)")
    args = p.parse_args()

    raw = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    migrated, dropped = migrate_pending(raw)

    Path(args.outp).write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"pending-мигратор: {len(raw)} юзеров → {len(migrated)} перенесено")
    for uid, keys in dropped.items():
        status = "ВЫКИНУТ ЦЕЛИКОМ" if uid not in migrated else f"выброшено {len(keys)} ключей"
        print(f"  user {uid}: {status} → {keys}")
    print(f"Результат: {args.outp}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
