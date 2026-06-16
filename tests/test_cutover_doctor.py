"""TDD: cutover_doctor — предбоевой gate перед Phase 3 cutover.

По CTO-ревью ChatGPT (C4/I4/I5) + risk-критику (ДЫРА #1 billing=Максим, G1
OAuth/telethon). Отдельный инструмент от tenant config_doctor: тот валидирует
КОНФИГ (статика), этот — СЕРВЕРНОЕ окружение/state (billing rows, deps,
команды, файлы токенов, чужие маркеры). Запускается на сервере перед cutover,
non-zero при любом blocker.

Здесь тестируются ЧИСТЫЕ check-функции (логика вердикта), отделённые от I/O
(сбор фактов с сервера — тонкая обвязка, проверяется запуском). Все факты
функциям подаются как аргументы → детерминированно, без сети/диска.

Запуск: python tests/test_cutover_doctor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import cutover_doctor as cd  # noqa: E402

_errs: list[str] = []


def _assert(cond, msg):
    print(f"  {'OK' if cond else 'X FAIL'} {msg}")
    if not cond:
        _errs.append(msg)


# ── 1. check_no_foreign_markers (N3 ревью) ──────────────────────────────────
def test_markers_finds_foreign():
    print("\n-- foreign markers: ловит чужие бренды --")
    text = '{"tenant_id":"panferov","channel":"@yumsunov_realbiz","x":"livedrive"}'
    found = cd.check_no_foreign_markers(text)
    _assert("yumsunov" in " ".join(found).lower(), "поймал yumsunov")
    _assert("livedrive" in " ".join(found).lower(), "поймал livedrive")


def test_markers_clean_panferov():
    print("\n-- foreign markers: чистый panferov-конфиг → пусто --")
    text = '{"tenant_id":"panferov","brands":{"allowed":["default","shoes"]}}'
    _assert(cd.check_no_foreign_markers(text) == [], "чистый конфиг → нет маркеров")


def test_markers_case_insensitive():
    print("\n-- foreign markers: регистронезависимо --")
    _assert(cd.check_no_foreign_markers("Life Drive проект") != [], "Life Drive пойман")


# ── 2. check_billing_owner (ДЫРА #1, I4) ────────────────────────────────────
def test_billing_owner_maksim_is_blocker():
    print("\n-- billing: база Максима → blocker --")
    rows = [{"telegram_id": 111, "username": "maksim_new", "display_name": "Максим", "bot_instance": "lifedrive"}]
    v = cd.check_billing_owner(rows, expected_instance="panferovai")
    _assert(v.get("level") == "blocker", f"чужой bot_instance lifedrive → blocker (got {v})")


def test_billing_owner_panferov_ok():
    print("\n-- billing: база Артёма → ok --")
    rows = [{"telegram_id": 384671843, "username": "artem", "display_name": "Артём", "bot_instance": "panferovai"}]
    v = cd.check_billing_owner(rows, expected_instance="panferovai")
    _assert(v.get("level") == "ok", f"свой instance → ok (got {v})")


def test_billing_owner_empty_warn():
    print("\n-- billing: пустая база → warn (не blocker) --")
    v = cd.check_billing_owner([], expected_instance="panferovai")
    _assert(v.get("level") == "warn", f"пустой clients → warn (got {v})")


# ── 3. check_deps (C4 deps) ─────────────────────────────────────────────────
def test_deps_missing_is_problem():
    print("\n-- deps: отсутствует пакет → problem --")
    installed = {"opencv-python-headless": "4.9.0"}
    required = {"opencv-python-headless": None, "apscheduler": None}
    probs = cd.check_deps(installed, required)
    _assert(any("apscheduler" in p.lower() for p in probs), "apscheduler отсутствует → problem")


def test_deps_all_present_ok():
    print("\n-- deps: всё на месте → пусто --")
    installed = {"opencv-python-headless": "4.9.0", "apscheduler": "3.10.4"}
    required = {"opencv-python-headless": None, "apscheduler": None}
    _assert(cd.check_deps(installed, required) == [], "все deps есть → нет проблем")


def test_deps_version_below_min():
    print("\n-- deps: версия ниже минимума → problem --")
    installed = {"elevenlabs": "2.30.0"}
    required = {"elevenlabs": "2.40.0"}
    probs = cd.check_deps(installed, required)
    _assert(any("elevenlabs" in p.lower() for p in probs), "версия 2.30 < 2.40 → problem")


# ── 4. check_commands_registered (I5) ───────────────────────────────────────
def test_commands_commented_out_detected():
    print("\n-- commands: закомментированная регистрация → отсутствует --")
    src = '''
    app.add_handler(CommandHandler("start", start))
    # app.add_handler(CommandHandler("launches", launches_command))
    app.add_handler(CommandHandler("brand", brand_command))
    '''
    missing = cd.check_commands_registered(src, expected={"start", "launches", "brand"})
    _assert("launches" in missing, "закомментированный /launches → в missing")
    _assert("start" not in missing, "активный /start → НЕ в missing")
    _assert("brand" not in missing, "активный /brand → НЕ в missing")


def test_commands_all_active():
    print("\n-- commands: все активны → пусто --")
    src = 'app.add_handler(CommandHandler("update", u)); app.add_handler(CommandHandler("report", r))'
    _assert(cd.check_commands_registered(src, expected={"update", "report"}) == [], "все активны → пусто")


# ── 5. check_files_present (G1) ─────────────────────────────────────────────
def test_files_missing_is_blocker():
    print("\n-- files: отсутствует токен → blocker --")
    states = {"telethon_session.session": "ok", "youtube_token.json": "missing"}
    probs = cd.check_files_present(states)
    _assert(any("youtube_token" in p for p in probs), "missing файл → в проблемах")


def test_files_corrupt_is_blocker():
    print("\n-- files: битый файл → blocker --")
    states = {"telethon_session.session": "corrupt"}
    probs = cd.check_files_present(states)
    _assert(any("telethon" in p for p in probs), "битый файл → в проблемах")


def test_files_all_ok():
    print("\n-- files: все ok → пусто --")
    states = {"a.session": "ok", "b.json": "ok"}
    _assert(cd.check_files_present(states) == [], "все ok → пусто")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"\n{'='*60}\nRunning {len(tests)} cutover_doctor tests\n{'='*60}")
    for fn in tests:
        try:
            fn()
        except Exception as e:
            _errs.append(f"{fn.__name__}: {e}")
            print(f"  X EXC {fn.__name__}: {e}")
    print(f"\n{'='*60}")
    print("ALL PASS" if not _errs else f"FAIL ({len(_errs)}): " + "; ".join(_errs))
    sys.exit(0 if not _errs else 1)
