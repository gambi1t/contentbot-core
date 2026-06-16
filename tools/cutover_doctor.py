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

# Чужие бренд-маркеры — в боевом env/tenant/prompts panferov их быть НЕ должно (N3).
_FOREIGN_MARKERS = [
    "maksim", "lifedrive", "livedrive", "life drive", "live drive",
    "yumsunov", "юмсунов", "лайф драйв",
]

# Минимальный набор зависимостей core, критичных для паритета фич Артёма.
REQUIRED_DEPS = {
    "opencv-python-headless": None,
    "apscheduler": None,             # PTB[job-queue] → launch_monitor cron
    "elevenlabs": "2.40.0",
    "faster-whisper": "1.2.1",
    "requests-toolbelt": "1.0.0",
}

# Команды Артёма, закомментированные в core «под Максима» — должны быть активны.
EXPECTED_COMMANDS = {"launches", "update", "report", "brand"}


# ── ЧИСТЫЕ check-функции (TDD) ───────────────────────────────────────────────

def check_no_foreign_markers(text: str) -> list[str]:
    """Найти чужие бренд-маркеры (maksim/livedrive/yumsunov/...) в тексте
    боевого конфига/env. Пусто = чисто."""
    low = (text or "").lower()
    return [m for m in _FOREIGN_MARKERS if m in low]


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


def check_files_present(states: dict) -> list[str]:
    """states = {path: 'ok'|'missing'|'corrupt'}. Вернуть всё, что не ok."""
    return [f"{path}: {st}" for path, st in states.items() if st != "ok"]


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

    # 4) commands registered
    bot_py = args.bot_py or str(Path(args.state_root) / "bot.py")
    if Path(bot_py).is_file():
        missing = check_commands_registered(
            Path(bot_py).read_text(encoding="utf-8", errors="replace"), EXPECTED_COMMANDS)
        if missing:
            blockers.append(f"[commands] не зарегистрированы (закомментированы): {missing}")
        else:
            print(f"[commands] все активны: {sorted(EXPECTED_COMMANDS)}")

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
