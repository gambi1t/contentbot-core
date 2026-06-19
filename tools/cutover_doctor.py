"""cutover_doctor — предбоевой gate перед Phase 3 cutover (panferov → core).

По CTO-ревью ChatGPT (C4/I4/I5) + risk-критику (ДЫРА #1 billing=Максим, G1
OAuth/telethon). Отличие от `tenant config_doctor`: тот валидирует КОНФИГ
(статика), этот — СЕРВЕРНОЕ окружение/state. Запускается НА СЕРВЕРЕ перед
боевым свитчем, non-zero при любом blocker.

    python -m tools.cutover_doctor --tenant panferov --state-root /root/contentbot-core \\
        --bot-py /root/contentbot-core/bot.py --billing-db /root/contentbot-core/billing/billing.db

Архитектура: ЧИСТЫЕ check-функции (логика вердикта, под TDD) + тонкий I/O-слой
(_cli собирает факты с диска/SQLite/окружения и кормит чистые функции).
"""
from __future__ import annotations

import sys

# Чужие бренд-маркеры — в боевом env/tenant/prompts panferov их быть НЕ должно
# (N3 + C3/C7 ревью: + контекст картинг/глэмпинг Life Drive и оранжевый акцент
# Максима #ff5722 — протечка в HF/Remotion-промптах). НЕ включаем «Постулат» —
# это бренд агентства самого Артёма, не чужой клиент.
_FOREIGN_MARKERS = [
    "maksim", "lifedrive", "livedrive", "life drive", "live drive",
    "yumsunov", "юмсунов", "лайф драйв",
    "karting", "картинг", "glamping", "глэмпинг", "#ff5722",
]

# Минимальный набор зависимостей core, критичных для паритета фич Артёма.
REQUIRED_DEPS = {
    "opencv-python-headless": None,
    "apscheduler": None,             # PTB[job-queue] → launch_monitor cron
    "elevenlabs": "2.40.0",
    "faster-whisper": "1.2.1",
    "requests-toolbelt": "1.0.0",
}

# Per-tenant команды. УСТАРЕЛО считать их «закомментированными под Максима»:
# с Фазы 2 они зарегистрированы УСЛОВНО по features (bot.main():22056-22077).
# Основная проверка теперь per-tenant (check_commands_per_tenant); это —
# generic-fallback (все present в коде) когда tenant.json не передан.
EXPECTED_COMMANDS = {"launches", "update", "report", "brand"}


# ── ЧИСТЫЕ check-функции (TDD) ───────────────────────────────────────────────

def check_no_foreign_markers(text: str) -> list[str]:
    """Найти чужие бренд-маркеры (maksim/livedrive/yumsunov/...) в тексте
    боевого конфига/env. Пусто = чисто."""
    low = (text or "").lower()
    return [m for m in _FOREIGN_MARKERS if m in low]


def scan_texts_for_markers(named_texts: dict) -> dict:
    """{name: text} → {name: [найденные маркеры]} только для файлов с хитами.

    Anti-leakage (C3/C7 ревью): чужой бренд Максима в АКТИВНЫХ prompt/reference/
    scene-файлах при tenant=panferov (HF reference_pack, Remotion-промпт/сцены).
    Чисто → {}.
    """
    out = {}
    for name, text in named_texts.items():
        hits = check_no_foreign_markers(text or "")
        if hits:
            out[name] = hits
    return out


def check_billing_owner(rows: list[dict], expected_instance: str) -> dict:
    """Вердикт по billing-базе (ДЫРА #1: в core лежит база Максима).
    blocker — есть клиент с чужим bot_instance; warn — пусто; ok — все свои."""
    if not rows:
        return {"level": "warn", "msg": "billing clients пуст (нет платящих — или не та база)"}
    foreign = sorted({r.get("bot_instance") for r in rows
                      if r.get("bot_instance") and r.get("bot_instance") != expected_instance})
    if foreign:
        return {"level": "blocker", "msg": f"чужие clients в billing: bot_instance={foreign} (ожидался {expected_instance!r})"}
    return {"level": "ok", "msg": f"billing — свой tenant ({expected_instance})"}


def _ver_tuple(v) -> tuple:
    out = []
    for part in str(v).split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    return tuple(out)


def check_deps(installed: dict, required: dict) -> list[str]:
    """Сравнить установленные пакеты с требуемыми. Отсутствует → problem;
    версия ниже минимума (если задан) → problem."""
    probs = []
    for pkg, minver in required.items():
        if pkg not in installed:
            probs.append(f"{pkg}: НЕ установлен")
            continue
        if minver and _ver_tuple(installed[pkg]) < _ver_tuple(minver):
            probs.append(f"{pkg}: версия {installed[pkg]} < требуемой {minver}")
    return probs


def check_commands_registered(src: str, expected: set[str]) -> list[str]:
    """Какие из expected-команд НЕ зарегистрированы (закомментированы) в bot.py.
    Активна = есть строка `CommandHandler("name"` без ведущего `#`."""
    missing = []
    for name in expected:
        pat_d, pat_s = f'CommandHandler("{name}"', f"CommandHandler('{name}'"
        active = any(
            (pat_d in line or pat_s in line) and not line.strip().startswith("#")
            for line in src.splitlines()
        )
        if not active:
            missing.append(name)
    return sorted(missing)


# Per-tenant команды (регистрируются условно по features — bot.main():22056-22077).
_PER_TENANT_COMMANDS = ("update", "report", "launches", "brand")


def expected_commands_for_tenant(tenant: dict) -> set[str]:
    """Какие per-tenant команды ДОЛЖНЫ быть активны у этого тенанта (по features).
    Зеркалит условия регистрации в bot.main() (C4 ревью):
      subscriber_stats → update+report · launch_monitor → launches ·
      brand_switch (>1 бренда) → brand.
    """
    import os
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import tenant as _t
    exp: set[str] = set()
    if not _t.feature_blocked(tenant, "subscriber_stats"):
        exp |= {"update", "report"}
    if not _t.feature_blocked(tenant, "launch_monitor"):
        exp.add("launches")
    if _t.brand_switch_available(tenant):
        exp.add("brand")
    return exp


def _present_commands(src: str) -> set[str]:
    """Какие per-tenant команды физически присутствуют в коде (строка
    `CommandHandler("name"` без ведущего `#`)."""
    return {name for name in _PER_TENANT_COMMANDS
            if not check_commands_registered(src, {name})}


def check_commands_per_tenant(src: str, tenant: dict) -> dict:
    """Per-tenant вердикт по командам (C4 ревью): для каждой per-tenant команды —
    expected (по features тенанта) vs present (в коде). Проблема ТОЛЬКО если
    команда ожидается, но в коде отсутствует/закомментирована. Не ожидается
    (фича выключена у тенанта) → ок, даже если строка есть в коде.

    Returns {"rows": {cmd: {expected, present, ok}}, "problems": [...]}.
    """
    expected = expected_commands_for_tenant(tenant)
    present = _present_commands(src)
    rows, problems = {}, []
    for cmd in _PER_TENANT_COMMANDS:
        exp = cmd in expected
        pres = cmd in present
        rows[cmd] = {"expected": exp, "present": pres, "ok": (not exp) or pres}
        if exp and not pres:
            problems.append(f"{cmd}: expected (по features) но НЕ найдена в коде")
    return {"rows": rows, "problems": problems}


def check_files_present(states: dict) -> list[str]:
    """states = {path: 'ok'|'missing'|'corrupt'}. Вернуть всё, что не ok."""
    return [f"{path}: {st}" for path, st in states.items() if st != "ok"]


def check_token_status(token_data: dict, now: float) -> dict:
    """Readiness OAuth-токена (C3/G1) — БЕЗ публикации, только статус.
    Логика истечения консистентна с ботом (crosspost: obtained_at+expires_in-300).

    - refresh_token есть → 'refreshable' (бот сам обновит после cutover) → ok.
    - нет refresh, истёк (obtained_at+expires_in < now) → 'expired' → blocker
      (нужна ручная переавторизация — постинг отвалится; напр. IG long-lived).
    - нет refresh, не истёк → 'valid' → ok.
    - нет refresh и нет expiry-полей → 'unknown' → warn (проверить вручную)."""
    if not isinstance(token_data, dict):
        return {"status": "unknown", "level": "warn", "msg": "не dict"}
    if token_data.get("refresh_token"):
        return {"status": "refreshable", "level": "ok", "msg": "есть refresh_token (бот обновит)"}
    obtained = token_data.get("obtained_at")
    expires_in = token_data.get("expires_in")
    if obtained is None or expires_in is None:
        return {"status": "unknown", "level": "warn", "msg": "нет refresh и нет obtained_at/expires_in — проверить вручную"}
    if now > obtained + expires_in - 300:
        return {"status": "expired", "level": "blocker", "msg": "истёк без refresh_token — нужна ручная переавторизация"}
    return {"status": "valid", "level": "ok", "msg": f"валиден ещё ~{int((obtained + expires_in - now) / 3600)}ч"}


# ── I/O-слой (сбор фактов на сервере; НЕ под TDD — проверяется запуском) ──────

def _cli() -> int:
    import argparse
    import json
    import sqlite3
    from pathlib import Path

    p = argparse.ArgumentParser(prog="cutover_doctor", description="Pre-cutover server/state gate")
    p.add_argument("--tenant", default="panferov")
    p.add_argument("--state-root", default=".")
    p.add_argument("--bot-py", default=None)
    p.add_argument("--billing-db", default=None)
    p.add_argument("--config", default=None, help="tenant.json для marker-скана")
    p.add_argument("--expected-instance", default="panferovai")
    args = p.parse_args()

    blockers, warns = [], []

    # 1) foreign markers в боевом конфиге
    if args.config and Path(args.config).is_file():
        m = check_no_foreign_markers(Path(args.config).read_text(encoding="utf-8", errors="replace"))
        if m:
            blockers.append(f"[markers] чужие бренды в {args.config}: {m}")
        else:
            print(f"[markers] {args.config}: чисто")

    # 2) billing owner
    if args.billing_db and Path(args.billing_db).is_file():
        try:
            con = sqlite3.connect(args.billing_db)
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute("SELECT * FROM clients")]
            con.close()
            v = check_billing_owner(rows, args.expected_instance)
            print(f"[billing] {v['level']}: {v['msg']}")
            if v["level"] == "blocker":
                blockers.append(f"[billing] {v['msg']}")
            elif v["level"] == "warn":
                warns.append(f"[billing] {v['msg']}")
        except Exception as e:
            warns.append(f"[billing] не прочитать {args.billing_db}: {e}")

    # 3) deps
    try:
        from importlib.metadata import version, PackageNotFoundError
        installed = {}
        for pkg in REQUIRED_DEPS:
            try:
                installed[pkg] = version(pkg)
            except PackageNotFoundError:
                pass
        probs = check_deps(installed, REQUIRED_DEPS)
        for pr in probs:
            blockers.append(f"[deps] {pr}")
        if not probs:
            print(f"[deps] все требуемые присутствуют: {sorted(installed)}")
    except Exception as e:
        warns.append(f"[deps] проверка не удалась: {e}")

    # 4) commands — per-tenant (C4): expected по features tenant.json, не «есть/нет вообще»
    bot_py = args.bot_py or str(Path(args.state_root) / "bot.py")
    if Path(bot_py).is_file():
        src = Path(bot_py).read_text(encoding="utf-8", errors="replace")
        _tcfg = None
        if args.config and Path(args.config).is_file():
            try:
                _tcfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
            except Exception:
                _tcfg = None
        if _tcfg is not None:
            v = check_commands_per_tenant(src, _tcfg)
            for cmd, r in sorted(v["rows"].items()):
                print(f"[commands] tenant={args.tenant} /{cmd}: expected={r['expected']} "
                      f"registered={r['present']} {'OK' if r['ok'] else 'PROBLEM'}")
            for pr in v["problems"]:
                blockers.append(f"[commands] {pr}")
        else:
            # fallback без tenant.json — generic-проверка (все per-tenant команды present)
            missing = check_commands_registered(src, EXPECTED_COMMANDS)
            if missing:
                blockers.append(f"[commands] не найдены в коде: {missing}")
            else:
                print(f"[commands] все per-tenant команды present (generic): {sorted(EXPECTED_COMMANDS)}")

    # 5) files present (OAuth/telethon) — собрать states
    states = {}
    root = Path(args.state_root)
    for name, kind in [("telethon_session.session", "sqlite"),
                       ("telethon_uploader_session.session", "sqlite")]:
        f = root / name
        if not f.is_file():
            continue  # необязательные — проверяем только если есть
        try:
            con = sqlite3.connect(str(f)); con.execute("PRAGMA integrity_check"); con.close()
            states[name] = "ok"
        except Exception:
            states[name] = "corrupt"
    for tok in root.glob("*token*.json"):
        try:
            json.loads(tok.read_text(encoding="utf-8")); states[tok.name] = "ok"
        except Exception:
            states[tok.name] = "corrupt"
    probs = check_files_present(states)
    for pr in probs:
        blockers.append(f"[files] {pr}")
    if states and not probs:
        print(f"[files] токены/сессии целостны: {sorted(states)}")

    # 5b) readiness OAuth-токенов (C3/G1 — не истёк ли, без публикации)
    import time
    now = time.time()
    for tok in root.glob("*token*.json"):
        try:
            td = json.loads(tok.read_text(encoding="utf-8"))
        except Exception:
            continue  # битый — уже учтён в files-секции выше
        v = check_token_status(td, now)
        print(f"[token] {tok.name}: {v['status']} — {v['msg']}")
        if v["level"] == "blocker":
            blockers.append(f"[token] {tok.name}: {v['msg']}")
        elif v["level"] == "warn":
            warns.append(f"[token] {tok.name}: {v['msg']}")

    # 6) tenant config_doctor (переиспользуем готовое)
    if args.config and Path(args.config).is_file():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            import tenant as _t
            cfg = _t.load_tenant(args.config, strict=True)
            cprobs = _t.config_doctor(cfg, expected_id=args.tenant, strict=True)
            for cp in cprobs:
                blockers.append(f"[config] {cp}")
            if not cprobs:
                print(f"[config] tenant.json валиден (strict, expected={args.tenant})")
        except Exception as e:
            blockers.append(f"[config] {e}")

    # 7) anti-leakage scan активных prompt/reference/scene (C3/C7) — для panferov
    # Чужой бренд Максима в дефолтных (активных при отсутствии panferov-варианта)
    # файлах = warn на паритете (движки OFF), станет blocker-гейтом в срезе C
    # перед включением HF/Remotion.
    if args.tenant == "panferov":
        named = {}
        for rel in ["hyperframes_assets/reference_pack.md", "auto_broll.py"]:
            f = root / rel
            if f.is_file():
                named[rel] = f.read_text(encoding="utf-8", errors="replace")
        hits = scan_texts_for_markers(named)
        if hits:
            for name, ms in hits.items():
                warns.append(f"[leakage] {name}: чужой бренд {ms} — нужен panferov-вариант ДО включения HF/Remotion")
        elif named:
            print(f"[leakage] активные prompt/reference чисты: {sorted(named)}")

    print("\n" + "=" * 60)
    if warns:
        print("WARN:")
        for w in warns:
            print(f"  ⚠ {w}")
    if blockers:
        print(f"BLOCKERS ({len(blockers)}) — NO-GO:")
        for b in blockers:
            print(f"  🔴 {b}")
        return 1
    print("GO: cutover-doctor green (с учётом warn выше)")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
