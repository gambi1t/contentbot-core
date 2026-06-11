"""
Рендер всех экранов с тестовыми данными — для визуального ревью UX.
Запуск:  PYTHONIOENCODING=utf-8 python -m billing.demo_screens
"""
from __future__ import annotations

import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path

os.environ["BILLING_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="demo_")) / "demo.db")
os.environ["ADMIN_TELEGRAM_IDS"] = "384671843"
os.environ["SUPPORT_CONTACT"] = "@postulataistudio"

from billing import api, db, keyboards, texts  # noqa: E402


def _plain(s: str) -> str:
    s = re.sub(r"<b>|</b>", "**", s)
    s = re.sub(r"<i>|</i>", "_", s)
    s = re.sub(r"<pre>|</pre>", "", s)
    s = re.sub(r"<code>|</code>", "`", s)
    s = re.sub(r"<a href=\"[^\"]+\">(.*?)</a>", r"\1", s)
    return s


def _render_inline(keyboard) -> str:
    if not keyboard or not hasattr(keyboard, "inline_keyboard"):
        return ""
    lines = []
    for row in keyboard.inline_keyboard:
        btns = " | ".join(f"[ {b.text} ]" for b in row)
        lines.append(f"   {btns}")
    return "\n".join(lines)


def _render_reply(keyboard) -> str:
    if not keyboard or not hasattr(keyboard, "keyboard"):
        return ""
    lines = []
    for row in keyboard.keyboard:
        btns = "   ".join(f"▐ {b.text} ▌" for b in row)
        lines.append(f"   {btns}")
    return "\n".join(lines)


def show(title: str, text: str, inline=None, reply=None):
    print("\n")
    print("┌" + "─" * 72)
    print(f"│  {title}")
    print("├" + "─" * 72)
    for line in _plain(text).split("\n"):
        print(f"│  {line}")
    if inline:
        print("├─── inline под сообщением ──────────────────────────────────────────────")
        print(_render_inline(inline))
    if reply:
        print("├─── reply-клавиатура (персистентная снизу) ─────────────────────────────")
        print(_render_reply(reply))
    print("└" + "─" * 72)


def main():
    db._wipe_all()
    api.init()

    # Данные для превью
    maksim = api.register_client(
        telegram_id=555,
        username="maksimlifedrive",
        display_name="Максим (Life Drive)",
        bot_instance="lifedrive",
        mode_default="full",
    )
    api.register_client(
        telegram_id=777, username=None, display_name="Обувной тест",
        bot_instance="shoestore", mode_default="self",
    )
    api.topup(555, Decimal(12000), admin_id=384671843, comment="входной пакет")
    api.register_video("vid-karting", 555, mode="full", title="Картинг с 7 лет")
    api.charge_video("vid-karting", trigger="crosspost")
    api.register_video("vid-glamping", 555, mode="full", title="5 причин глэмпинга")
    api.charge_video("vid-glamping", trigger="download_final")
    api.register_video("vid-selfie-1", 555, mode="selfie", title="Ответ про трассу")
    api.charge_video("vid-selfie-1", trigger="crosspost")

    # ═══ ЧАСТЬ 1 — БОТ ИЛАНА (Артём) ══════════════════════════════════════
    print("\n" + "═" * 74)
    print("  ЧАСТЬ 1 — БОТ АРТЁМА @panferovai_contentbot")
    print("═" * 74)

    show(
        "1.1 Главный экран Илана (/start)",
        "👋 <b>Илан | Контент-завод</b>\n\n"
        "Выбирай действие на клавиатуре снизу.\n"
        "Через ☰ меню — редкие команды: /stats, /calendar, /report, /pub, /ideas.",
        reply=keyboards.ilan_main(),
    )

    # ═══ ЧАСТЬ 2 — БОТ МАКСИМА (клиентский) ═══════════════════════════════
    print("\n" + "═" * 74)
    print("  ЧАСТЬ 2 — БОТ КЛИЕНТА @lifedrive_contentbot (Максим)")
    print("═" * 74)

    client = api.get_client(555)
    balance = api.get_balance(555)

    show(
        "2.1 Главный экран Максима (/start)",
        texts.client_main_menu(client, balance),
        reply=keyboards.client_main(),
    )

    history = api.get_history(555, 10)
    stats = api.get_client_stats(555)
    show(
        "2.2 Баланс (tap на «💰 Баланс»)",
        texts.client_balance(client, balance, history, stats),
        inline=keyboards.client_balance(),
        reply=keyboards.client_main(),
    )

    show(
        "2.3 Запрос счёта — шаг 1 (tap «💳 Запросить счёт»)",
        texts.client_request_topup_prompt(),
        inline=keyboards.client_request_topup_cancel(),
    )

    show(
        "2.4 Запрос счёта — шаг 2 (клиент ввёл «20000»)",
        texts.client_request_topup_sent(Decimal(20000)),
        reply=keyboards.client_main(),
    )

    # Сценарий низкого баланса
    api.register_client(
        telegram_id=888, username="lowbal", display_name="Тестовый (низкий)",
        bot_instance="lifedrive", mode_default="full",
    )
    api.topup(888, Decimal(200), admin_id=384671843, comment="")
    show(
        "2.5 «Недостаточно средств» (нажал «🎬 Новый ролик» с балансом 200₽)",
        texts.client_insufficient(Decimal(200), "full"),
        inline=keyboards.client_insufficient("full"),
        reply=keyboards.client_main(),
    )

    show(
        "2.6 «Недостаточно на селфи» (попытка селфи с балансом 100₽)",
        texts.client_insufficient(Decimal(100), "selfie"),
        inline=keyboards.client_insufficient("selfie"),
        reply=keyboards.client_main(),
    )

    show(
        "2.7 Уведомление о пополнении (push после пополнения админом)",
        texts.client_topup_notification(
            amount=Decimal(12000), new_balance=Decimal(12000),
            comment="Пополнение от менеджера",
        ),
        reply=keyboards.client_main(),
    )

    show(
        "2.8 Уведомление о списании",
        texts.client_charge_notification(
            title="Картинг с 7 лет", amount=Decimal(2500),
            new_balance=Decimal(7000), trigger="crosspost",
        ),
        reply=keyboards.client_main(),
    )

    show(
        "2.9 Предупреждение про сырой материал (скачал аватар/voice/broll)",
        texts.raw_asset_warning(),
        reply=keyboards.client_main(),
    )

    show(
        "2.10 Доступ ограничен (незнакомый tg_id)",
        texts.client_unknown(tg_id=123456),
    )

    # ═══ ЧАСТЬ 3 — СЕЛФИ ФЛОУ ═════════════════════════════════════════════
    print("\n" + "═" * 74)
    print("  ЧАСТЬ 3 — СЕЛФИ ФЛОУ (как у Максима, так и у Илана)")
    print("═" * 74)

    show(
        "3.1 Нажал «🎥 Селфи» — бот просит видео",
        texts.selfie_upload_prompt(balance),
        reply=keyboards.client_main(),
    )

    show(
        "3.2 Клиент прислал видео — выбор пути",
        texts.selfie_path_choice(duration_sec=28, chars=412),
        inline=keyboards.selfie_path_choice(),
    )

    show(
        "3.3 Идёт сборка (выбрал «🎬 + B-roll»)",
        texts.selfie_processing(path="broll"),
    )

    show(
        "3.4 Селфи готов",
        texts.selfie_ready(title="Ответ про трассу"),
        inline=keyboards.selfie_done(),
        reply=keyboards.client_main(),
    )

    # ═══ ЧАСТЬ 4 — АДМИНКА (у Артёма, команда /admin) ═══════════════════════
    print("\n" + "═" * 74)
    print("  ЧАСТЬ 4 — АДМИНКА (бот Артёма, /admin)")
    print("═" * 74)

    show(
        "4.1 Вход в админку (/admin)",
        texts.admin_menu(),
        reply=keyboards.admin_reply_menu(),
    )

    clients = api.list_clients()
    balances = {c.telegram_id: api.get_balance(c.telegram_id) for c in clients}
    show(
        "4.2 Список клиентов (tap «👥 Клиенты»)",
        texts.admin_clients_list(clients, balances),
        inline=keyboards.admin_clients_list(clients),
        reply=keyboards.admin_reply_menu(),
    )

    card_stats = api.get_client_stats(555)
    card_hist = api.get_history(555, 10)
    show(
        "4.3 Карточка клиента (tap на «Максим»)",
        texts.admin_client_card(client, balance, card_hist, card_stats),
        inline=keyboards.admin_client_card(client),
        reply=keyboards.admin_reply_menu(),
    )

    show(
        "4.4 Пополнение — шаг 1 (tap «➕ Пополнить»)",
        texts.admin_topup_prompt(client),
        inline=keyboards.admin_add_client_cancel(),
    )

    show(
        "4.5 Пополнение — шаг 2 (ввёл «12000»)",
        texts.admin_topup_confirm(client, Decimal(12000)),
        inline=keyboards.admin_topup_confirm(client.telegram_id),
    )

    show(
        "4.6 Пополнение — готово",
        texts.admin_topup_done(client, Decimal(12000), balance + Decimal(12000)),
        inline=keyboards.admin_client_card(client),
    )

    show(
        "4.7 Корректировка — ввод суммы",
        texts.admin_adjust_amount_prompt(client),
        inline=keyboards.admin_add_client_cancel(),
    )

    show(
        "4.8 Корректировка — ввод комментария (после «-500»)",
        texts.admin_adjust_comment_prompt(Decimal(-500)),
        inline=keyboards.admin_add_client_cancel(),
    )

    show(
        "4.9 Корректировка — подтверждение",
        texts.admin_adjust_confirm(client, Decimal(-500), "ошибочно начислили"),
        inline=keyboards.admin_adjust_confirm(client.telegram_id),
    )

    show(
        "4.10 Смена режима клиента",
        f"⚙️ <b>Режим по умолчанию · {client.display_name}</b>\n\n"
        "Выберите режим — по нему будет идти списание за каждый новый НЕ-селфи ролик. "
        "Селфи всегда 150₽ независимо от дефолта.",
        inline=keyboards.admin_mode_select(client),
    )

    show(
        "4.11 Новый клиент — шаг 1 (tap «➕ Новый клиент»)",
        texts.admin_add_client_tg_id_prompt(),
        inline=keyboards.admin_add_client_cancel(),
    )

    show(
        "4.12 Новый клиент — шаг 2 (ввёл TG ID)",
        texts.admin_add_client_name_prompt(999123),
        inline=keyboards.admin_add_client_cancel(),
    )

    show(
        "4.13 Новый клиент — шаг 3 (ввёл имя)",
        texts.admin_add_client_instance_prompt("Иван (Кофейня)"),
        inline=keyboards.admin_add_client_cancel(),
    )

    show(
        "4.14 Новый клиент — шаг 4 (ввёл инстанс)",
        "Выберите режим по умолчанию:",
        inline=keyboards.admin_add_client_mode(),
    )

    show(
        "4.15 Новый клиент — шаг 5 (подтверждение)",
        texts.admin_add_client_confirm(999123, "Иван (Кофейня)", "coffee", "self"),
        inline=keyboards.admin_add_client_confirm(),
    )

    show(
        "4.16 Отчёт — выбор периода",
        "📊 <b>Отчёт по клиентам</b>\n\nВыберите период:",
        inline=keyboards.admin_report_periods(),
    )

    rep = api.report(period="all")
    show(
        "4.17 Отчёт за всё время",
        texts.admin_report(rep),
        inline=keyboards.admin_report_periods(),
    )

    show(
        "4.18 PUSH админу — клиент запросил счёт",
        texts.admin_notify_topup_request(client, Decimal(20000), balance),
        inline=keyboards.admin_topup_from_notification(client.telegram_id),
    )

    print("\n" + "═" * 74)
    print("  Конец превью. Экранов: 32 (1 Илан + 10 клиент + 4 селфи + 18 админ).")
    print("═" * 74)


if __name__ == "__main__":
    main()
