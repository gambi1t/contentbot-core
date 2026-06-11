"""
Смоук-тест биллинг-модуля. Проверяет ключевые сценарии end-to-end.
Запуск: python -m billing.tests.test_billing

НЕ трогает рабочую billing.db — использует временный файл.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal
from pathlib import Path


def _setup_tmp_db():
    """Подменяет BILLING_DB_PATH на временный файл до импорта модулей."""
    tmp = Path(tempfile.mkdtemp(prefix="billing_test_")) / "test.db"
    os.environ["BILLING_DB_PATH"] = str(tmp)
    return tmp


TMP_DB = _setup_tmp_db()

# import ПОСЛЕ подмены env
from billing import api, db  # noqa: E402


def assert_eq(actual, expected, label=""):
    if actual != expected:
        raise AssertionError(f"[{label}] expected {expected!r}, got {actual!r}")
    print(f"  ✓ {label}: {actual}")


def main():
    print(f"Test DB: {TMP_DB}")
    db._wipe_all()
    api.init()
    print("✓ init: таблицы созданы")

    # ── клиент ──────────────────────────────────────────────────────────────
    print("\n[1] register_client")
    client = api.register_client(
        telegram_id=111,
        username="maksim",
        display_name="Максим (Life Drive)",
        bot_instance="lifedrive",
        mode_default="self",
    )
    assert_eq(client.display_name, "Максим (Life Drive)", "display_name")
    assert_eq(client.mode_default, "self", "mode_default")

    # повторный вызов — должен просто обновить данные, не дублировать
    client2 = api.register_client(
        telegram_id=111, username="maksim_new", display_name="Максим", bot_instance="lifedrive"
    )
    assert_eq(client2.id, client.id, "same id on re-register")
    assert_eq(client2.username, "maksim_new", "username updated")

    # ── баланс пуст ─────────────────────────────────────────────────────────
    print("\n[2] начальный баланс")
    assert_eq(api.get_balance(111), Decimal(0), "balance=0")

    ok, reason, price = api.can_create_video(111, mode="self")
    assert_eq(ok, False, "can_create_video → False (нет денег)")
    assert_eq(reason, "insufficient_balance", "reason")
    assert_eq(price, Decimal(350), "price self=350")

    # ── пополнение ──────────────────────────────────────────────────────────
    print("\n[3] topup 12000")
    new_balance = api.topup(111, Decimal(12000), admin_id=999, comment="входной пакет")
    assert_eq(new_balance, Decimal(12000), "balance after topup")

    ok, reason, _ = api.can_create_video(111, mode="full")
    assert_eq(ok, True, "can_create_video full (баланса хватит)")

    # ── создание ролика + списание ──────────────────────────────────────────
    print("\n[4] register_video + charge")
    api.register_video("vid-001", telegram_id=111, mode="self", title="Картинг с 7 лет")
    result = api.charge_video("vid-001", trigger="crosspost")
    assert_eq(result.status, "charged", "status=charged")
    assert_eq(result.amount_rub, Decimal(350), "amount=350")
    assert_eq(result.new_balance, Decimal(11650), "balance=11650")

    # ── идемпотентность: повтор не списывает ────────────────────────────────
    print("\n[5] повторный charge — должен вернуть already_charged")
    result2 = api.charge_video("vid-001", trigger="download_final")
    assert_eq(result2.status, "already_charged", "status=already_charged")
    assert_eq(api.get_balance(111), Decimal(11650), "balance не изменился")

    # ── full-режим ролик ─────────────────────────────────────────────────────
    print("\n[6] ролик в full-режиме")
    api.register_video("vid-002", telegram_id=111, mode="full", title="5 причин глэмпинга")
    result3 = api.charge_video("vid-002", trigger="download_final")
    assert_eq(result3.status, "charged", "status")
    assert_eq(result3.amount_rub, Decimal(2500), "full price")
    assert_eq(result3.new_balance, Decimal(9150), "balance=9150")

    # ── недостаток средств ───────────────────────────────────────────────────
    print("\n[7] списание в минус — не должно пройти")
    # создаём ролик когда денег на full хватает, но тратим до недостатка
    for i in range(3, 7):
        api.register_video(f"vid-00{i}", telegram_id=111, mode="full", title=f"#{i}")
        api.charge_video(f"vid-00{i}", trigger="crosspost")
    # сейчас баланс 9150 - 4*2500 = -850 — но наш код не даёт уйти в минус:
    # последний из четырёх провалится
    # Перепроверю — посчитаю явно
    balance_now = api.get_balance(111)
    print(f"   баланс после 4 попыток: {balance_now}")
    # Должно быть 9150 - 2500*3 = 1650 (3 списания прошли, 4-е упало на insufficient)
    assert_eq(balance_now, Decimal(1650), "balance after 3 successful charges")

    # четвёртый full-ролик не списался (vid-006)
    # До этого точно прошли: vid-001 (self), vid-002 (full), vid-003/004/005 (full) = 5 списаний
    row_state = api.get_history(111, limit=20)
    charges = [op for op in row_state if op.type == "charge"]
    assert_eq(len(charges), 5, "5 successful charges (vid-001 self + vid-002..005 full)")
    # vid-006 должен быть insufficient
    from billing.db import get_conn
    with get_conn() as conn:
        v6 = conn.execute("SELECT charged FROM videos WHERE id = 'vid-006'").fetchone()
        assert_eq(v6["charged"], 0, "vid-006 не оплачен (insufficient)")

    # ── refund ──────────────────────────────────────────────────────────────
    print("\n[8] refund за брак")
    new_bal = api.refund_video("vid-002", admin_id=999, comment="клиент пожаловался")
    assert_eq(new_bal, Decimal(4150), "balance после refund (1650+2500)")

    # ── история ─────────────────────────────────────────────────────────────
    print("\n[9] история операций")
    history = api.get_history(111, limit=5)
    print(f"   всего операций: {len(history)}, последние 5:")
    for op in history:
        sign = "+" if op.amount_rub > 0 else ""
        print(f"   {sign}{op.amount_rub} ₽ · {op.type} · {op.created_at} · {op.comment}")

    # ── отчёт ───────────────────────────────────────────────────────────────
    print("\n[10] report")
    rep = api.report(period="all", bot_instance="lifedrive")
    assert_eq(len(rep["by_client"]), 1, "1 клиент")
    row = rep["by_client"][0]
    print(f"   {row['display_name']}: +{row['topups']} / -{row['charges']} / роликов {row['videos']} / баланс {row['balance']}")
    assert_eq(row["balance"], Decimal(4150), "итоговый баланс")
    # После refund vid-002 оплаченных: vid-001, vid-003, vid-004, vid-005 = 4
    assert_eq(row["videos"], 4, "4 оплаченных ролика (refund снял vid-002)")

    # ── can_create_video с нулевым балансом ─────────────────────────────────
    print("\n[11] блокировка при низком балансе")
    # Баланс 4150, full=2500 → можно. Потратим до <350
    api.manual_adjust(111, Decimal(-3900), admin_id=999, comment="тест блокировки")
    bal = api.get_balance(111)
    print(f"   баланс: {bal}")
    ok_self, _, _ = api.can_create_video(111, mode="self")
    ok_full, _, _ = api.can_create_video(111, mode="full")
    assert_eq(ok_self, False, "self заблокирован (<350)")
    assert_eq(ok_full, False, "full заблокирован (<2500)")

    print("\n✅ Все тесты прошли.")


if __name__ == "__main__":
    main()
