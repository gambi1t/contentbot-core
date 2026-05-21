"""
billing.texts — все строки сообщений в одном месте.

Parse mode — HTML. Используются теги: <b>, <i>, <code>, <pre>, <a href="">.
Функции, принимающие параметры, возвращают готовую строку.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable

from billing.api import BalanceOp, Client, PRICES_RUB
from billing.config import SUPPORT_CONTACT


# ─── formatters ───────────────────────────────────────────────────────────

def fmt_rub(amount: Decimal | int | float, signed: bool = False) -> str:
    """'12 000 ₽' или '+12 000 ₽' / '−2 500 ₽'."""
    d = Decimal(str(amount))
    sign = ""
    if signed:
        if d > 0:
            sign = "+"
        elif d < 0:
            sign = "−"
            d = -d
    # целые — без копеек, дробные — до 2 знаков
    if d == d.to_integral_value():
        n = int(d)
        formatted = f"{n:,}".replace(",", " ")
    else:
        formatted = f"{d:,.2f}".replace(",", " ").replace(".", ",")
    return f"{sign}{formatted} ₽"


def fmt_date(iso: str) -> str:
    """'2026-04-18 14:30:12' → '18 апр 14:30'."""
    try:
        dt = datetime.fromisoformat(iso.replace(" ", "T"))
    except ValueError:
        return iso
    months = ["янв", "фев", "мар", "апр", "май", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек"]
    return f"{dt.day} {months[dt.month-1]} {dt.strftime('%H:%M')}"


def _videos_affordable(balance: Decimal) -> tuple[int, int, int]:
    """Сколько роликов хватит в self, full и selfie."""
    self_n = int(balance // PRICES_RUB["self"]) if balance > 0 else 0
    full_n = int(balance // PRICES_RUB["full"]) if balance > 0 else 0
    selfie_n = int(balance // PRICES_RUB["selfie"]) if balance > 0 else 0
    return self_n, full_n, selfie_n


# ─── CLIENT TEXTS ─────────────────────────────────────────────────────────

def client_main_menu(client: Client, balance: Decimal) -> str:
    self_n, full_n, selfie_n = _videos_affordable(balance)
    mode_label = "full-service" if client.mode_default == "full" else "self-service"
    price = PRICES_RUB[client.mode_default]
    return (
        f"👋 Привет, <b>{client.display_name}</b>!\n\n"
        f"💰 Баланс: <b>{fmt_rub(balance)}</b>\n"
        f"📽 Режим: <b>{mode_label}</b> ({fmt_rub(price)}/ролик)\n"
        f"🧮 Хватит на:\n"
        f"   • <b>{full_n}</b> full-роликов (2 500 ₽)\n"
        f"   • <b>{self_n}</b> self-роликов (350 ₽)\n"
        f"   • <b>{selfie_n}</b> селфи-роликов (150 ₽)\n\n"
        f"Выбери действие на клавиатуре ↓"
    )


def client_unknown(tg_id: int) -> str:
    return (
        "🚫 <b>Доступ ограничен</b>\n\n"
        "Этот бот работает только по приглашению.\n"
        "Свяжитесь с менеджером: " + SUPPORT_CONTACT + "\n\n"
        f"Ваш Telegram ID: <code>{tg_id}</code>\n"
        "<i>(перешлите этот ID менеджеру, чтобы вас зарегистрировали)</i>"
    )


def client_balance(client: Client, balance: Decimal, history: list[BalanceOp], stats: dict) -> str:
    self_n, full_n, selfie_n = _videos_affordable(balance)

    hist_lines = []
    for op in history:
        date = fmt_date(op.created_at)
        amount = fmt_rub(op.amount_rub, signed=True)
        if op.type == "topup":
            label = op.comment or "Пополнение"
        elif op.type == "charge":
            mode_short = "full" if op.mode == "full" else "self"
            label = f"{op.comment or 'Ролик'} · {mode_short}"
        elif op.type == "refund":
            label = f"Возврат · {op.comment or 'без комментария'}"
        elif op.type == "manual_adjust":
            label = f"Корректировка · {op.comment}"
        else:
            label = op.type
        hist_lines.append(f"{amount:>14}  · {date} · {label}")

    history_block = (
        "<pre>" + "\n".join(hist_lines) + "</pre>"
        if hist_lines
        else "<i>операций пока нет</i>"
    )

    return (
        f"💰 <b>Ваш баланс</b>\n"
        f"<code>  {fmt_rub(balance)}</code>\n\n"
        f"🧮 Хватит на:\n"
        f"  • <b>{full_n}</b> роликов · full-service (2 500 ₽)\n"
        f"  • <b>{self_n}</b> роликов · self-service (350 ₽)\n"
        f"  • <b>{selfie_n}</b> селфи-роликов (150 ₽)\n\n"
        f"📊 <b>История</b> (последние {len(history)}):\n"
        f"{history_block}\n\n"
        f"📈 <b>За всё время</b>:\n"
        f"  Пополнено: {fmt_rub(stats['topups_rub'])}\n"
        f"  Потрачено: {fmt_rub(stats['charges_rub'])}\n"
        f"  Роликов: {stats['videos_charged']}"
    )


def client_request_topup_prompt() -> str:
    return (
        "💳 <b>Запрос на пополнение</b>\n\n"
        "Укажите сумму в рублях, на которую хотите пополнить баланс.\n"
        "<i>Например: 12000</i>\n\n"
        "Мы получим заявку и вышлем счёт от ИП Панфёров. "
        "После оплаты баланс будет пополнен автоматически."
    )


def client_request_topup_sent(amount: Decimal) -> str:
    return (
        f"✅ <b>Заявка отправлена</b>\n\n"
        f"Сумма: <b>{fmt_rub(amount)}</b>\n\n"
        f"Менеджер {SUPPORT_CONTACT} свяжется с вами для оплаты в ближайшее время."
    )


def client_insufficient(balance: Decimal, mode: str) -> str:
    price = PRICES_RUB[mode]
    mode_label = {
        "full": "full-service (2 500 ₽)",
        "self": "self-service (350 ₽)",
        "selfie": "селфи-ролик (150 ₽)",
    }.get(mode, mode)
    return (
        f"🚫 <b>Недостаточно средств</b>\n\n"
        f"На балансе: <b>{fmt_rub(balance)}</b>\n"
        f"Минимум: <b>{fmt_rub(price)}</b> · {mode_label}\n\n"
        f"Пополните баланс через кнопку ниже или свяжитесь с {SUPPORT_CONTACT}."
    )


def client_settings(client: Client) -> str:
    mode_label = "full-service" if client.mode_default == "full" else "self-service"
    return (
        f"⚙️ <b>Настройки</b>\n\n"
        f"Режим по умолчанию: <b>{mode_label}</b>\n\n"
        f"<b>self-service</b> — 350 ₽/ролик. Вы сами утверждаете сценарий, "
        f"сами публикуете на площадках.\n\n"
        f"<b>full-service</b> — 2 500 ₽/ролик. Мы ведём ролик за вас, "
        f"вы утверждаете только готовый результат."
    )


def client_mode_changed(mode: str) -> str:
    mode_label = "full-service (2 500 ₽/ролик)" if mode == "full" else "self-service (350 ₽/ролик)"
    return f"✅ Режим изменён: <b>{mode_label}</b>"


def client_topup_notification(amount: Decimal, new_balance: Decimal, comment: str) -> str:
    """Уведомление клиенту о том, что админ пополнил баланс."""
    note = f"\n<i>{comment}</i>" if comment else ""
    return (
        f"💰 <b>Баланс пополнен</b>\n\n"
        f"На счёт зачислено: <b>{fmt_rub(amount, signed=True)}</b>{note}\n"
        f"Текущий баланс: <b>{fmt_rub(new_balance)}</b>"
    )


def client_charge_notification(title: str, amount: Decimal, new_balance: Decimal, trigger: str) -> str:
    trigger_label = {
        "crosspost": "опубликован на площадках",
        "download_final": "готовый ролик скачан",
        "download_zip": "материалы скачаны",
    }.get(trigger, trigger)
    return (
        f"📽 <b>Ролик готов</b>\n\n"
        f"«{title}» — {trigger_label}.\n"
        f"Списано: <b>{fmt_rub(-amount, signed=True)}</b>\n"
        f"Остаток: <b>{fmt_rub(new_balance)}</b>"
    )


def raw_asset_warning() -> str:
    return (
        "⚠️ <b>Это сырой материал</b>\n\n"
        "Без монтажа, субтитров и музыки — оплата не берётся.\n"
        "Готовый ролик в сборке → кнопка «🎬 Готово к публикации» или «📥 Скачать материалы».\n"
        "Списание идёт только за финальный ролик."
    )


# ─── SELFIE FLOW ──────────────────────────────────────────────────────────

def selfie_upload_prompt(balance: Decimal) -> str:
    return (
        "🎥 <b>Селфи-ролик · 150 ₽</b>\n\n"
        "Пришли видео говорящей головы (как ты снимаешь себя на телефон).\n"
        "Я сделаю: субтитры, обложку, музыку — и готово к публикации.\n\n"
        f"💰 Баланс: <b>{fmt_rub(balance)}</b> (хватит на "
        f"{int(balance // PRICES_RUB['selfie']) if balance > 0 else 0} селфи)\n\n"
        "<i>Советы: снимай в тихом месте, ровный свет, держи телефон вертикально.</i>"
    )


def selfie_path_choice(duration_sec: int, chars: int) -> str:
    return (
        "✅ <b>Видео получено</b>\n\n"
        f"Длительность: <b>{duration_sec} сек</b>\n"
        f"Распознано: <b>{chars} знаков</b> текста\n\n"
        "Как собрать ролик?\n\n"
        "🎤 <b>Только субтитры</b> — твоё видео + субтитры + обложка + музыка.\n\n"
        "🎬 <b>+ B-roll</b> — то же, плюс AI подберёт стоковые видео-вставки "
        "на ключевые моменты (поверх твоего видео). Бесплатно.\n\n"
        "<i>Стоимость одинаковая: 150 ₽ в обоих случаях.</i>"
    )


def selfie_processing(path: str) -> str:
    label = {
        "plain": "только субтитры",
        "broll": "субтитры + B-roll",
    }.get(path, path)
    return (
        f"⚙️ <b>Собираю селфи-ролик...</b>\n\n"
        f"Вариант: <b>{label}</b>\n"
        "⏳ Обычно 1–2 минуты."
    )


def selfie_ready(title: str) -> str:
    return (
        "🎬 <b>Селфи-ролик готов</b>\n\n"
        f"«{title}»\n\n"
        "Выбери: опубликовать сразу на площадки или сначала скачать?\n"
        "<i>Списание 150 ₽ произойдёт после публикации или скачивания.</i>"
    )


# ─── ADMIN TEXTS ──────────────────────────────────────────────────────────

def admin_menu() -> str:
    return (
        "👑 <b>Админка биллинга</b>\n\n"
        "Выберите раздел:"
    )


def admin_clients_list(clients: list[Client], balances: dict[int, Decimal]) -> str:
    if not clients:
        return (
            "👥 <b>Клиенты</b>\n\n"
            "<i>Ни одного клиента не зарегистрировано.</i>\n"
            "Нажмите «➕ Добавить клиента», чтобы зарегистрировать."
        )
    lines = [f"👥 <b>Клиенты</b> — активных: {len(clients)}\n"]
    for c in clients:
        bal = balances.get(c.telegram_id, Decimal(0))
        mode = "full" if c.mode_default == "full" else "self"
        uname = f"@{c.username}" if c.username else f"id:{c.telegram_id}"
        lines.append(f"• <b>{c.display_name}</b> · {uname} · {fmt_rub(bal)} · {mode}")
    return "\n".join(lines)


def admin_client_card(client: Client, balance: Decimal, history: list[BalanceOp], stats: dict) -> str:
    uname = f"@{client.username}" if client.username else "—"
    status = "✅ активен" if client.is_active else "🚫 деактивирован"
    mode_label = "full-service" if client.mode_default == "full" else "self-service"

    hist_lines = []
    for op in history[:5]:
        date = fmt_date(op.created_at)
        amount = fmt_rub(op.amount_rub, signed=True)
        label = op.comment or op.type
        hist_lines.append(f"{amount:>14}  · {date} · {label}")
    hist_block = (
        "<pre>" + "\n".join(hist_lines) + "</pre>"
        if hist_lines
        else "<i>операций нет</i>"
    )

    return (
        f"👤 <b>{client.display_name}</b>\n\n"
        f"Username: {uname}\n"
        f"TG ID: <code>{client.telegram_id}</code>\n"
        f"Бот: <code>{client.bot_instance}</code>\n"
        f"Статус: {status}\n"
        f"Режим: <b>{mode_label}</b>\n\n"
        f"💰 Баланс: <b>{fmt_rub(balance)}</b>\n\n"
        f"📊 История (последние 5):\n{hist_block}\n\n"
        f"📈 Всего: +{fmt_rub(stats['topups_rub'])} / −{fmt_rub(stats['charges_rub'])} / {stats['videos_charged']} роликов"
    )


def admin_topup_prompt(client: Client) -> str:
    return (
        f"➕ <b>Пополнение · {client.display_name}</b>\n\n"
        f"Введите сумму в рублях (например <code>12000</code>):"
    )


def admin_topup_confirm(client: Client, amount: Decimal) -> str:
    return (
        f"❓ Пополнить баланс <b>{client.display_name}</b> на <b>{fmt_rub(amount)}</b>?\n\n"
        f"После подтверждения клиенту придёт уведомление в бот."
    )


def admin_topup_done(client: Client, amount: Decimal, new_balance: Decimal) -> str:
    return (
        f"✅ Баланс пополнен\n\n"
        f"<b>{client.display_name}</b>: {fmt_rub(amount, signed=True)}\n"
        f"Новый баланс: <b>{fmt_rub(new_balance)}</b>\n\n"
        f"<i>Клиент уведомлён в боте.</i>"
    )


def admin_adjust_amount_prompt(client: Client) -> str:
    return (
        f"➖ <b>Корректировка · {client.display_name}</b>\n\n"
        f"Введите сумму. Положительная — начисление, отрицательная — списание.\n"
        f"<i>Примеры: <code>500</code> или <code>-500</code></i>"
    )


def admin_adjust_comment_prompt(amount: Decimal) -> str:
    return (
        f"📝 Сумма: <b>{fmt_rub(amount, signed=True)}</b>\n\n"
        f"Напишите комментарий (обязательно — он останется в истории операций):"
    )


def admin_adjust_confirm(client: Client, amount: Decimal, comment: str) -> str:
    return (
        f"❓ Подтвердите:\n\n"
        f"Клиент: <b>{client.display_name}</b>\n"
        f"Сумма: <b>{fmt_rub(amount, signed=True)}</b>\n"
        f"Комментарий: <i>{comment}</i>"
    )


def admin_add_client_tg_id_prompt() -> str:
    return (
        "➕ <b>Новый клиент</b>\n\n"
        "Нужен <b>Telegram ID</b> клиента — числовой, например "
        "<code>384671843</code>.\n\n"
        "По нику (<code>@username</code>) без действия клиента получить ID "
        "нельзя — это ограничение Telegram.\n\n"
        "<b>3 способа добыть ID:</b>\n\n"
        "1️⃣ <b>Перешли сюда любое сообщение от клиента</b> "
        "(Forward, не копия) — я сам вытащу ID из метаданных.\n\n"
        "2️⃣ <b>Попроси клиента написать нашему боту</b> "
        "<code>/start</code>. Он получит отказ с текстом «Ваш Telegram ID: "
        "12345678» — пусть перешлёт тебе это число.\n\n"
        "3️⃣ <b>@userinfobot</b> — перешли ему сообщение клиента, "
        "он ответит числовым ID.\n\n"
        "Когда будет ID — пришли его в этот чат."
    )


def admin_add_client_name_prompt(tg_id: int) -> str:
    return (
        f"TG ID: <code>{tg_id}</code>\n\n"
        f"Введите имя клиента (как будет отображаться в админке):\n"
        f"<i>Например: «Максим (Life Drive)»</i>"
    )


def admin_add_client_instance_prompt(name: str) -> str:
    return (
        f"Имя: <b>{name}</b>\n\n"
        f"Введите код инстанса бота, в котором работает клиент:\n"
        f"<i>Например: <code>lifedrive</code>, <code>shoestore</code>, <code>panferovai</code></i>"
    )


def admin_add_client_confirm(tg_id: int, name: str, instance: str, mode: str) -> str:
    return (
        f"❓ Создать клиента?\n\n"
        f"Имя: <b>{name}</b>\n"
        f"TG ID: <code>{tg_id}</code>\n"
        f"Бот: <code>{instance}</code>\n"
        f"Режим по умолчанию: <b>{mode}</b>"
    )


def admin_client_created(client: Client) -> str:
    return (
        f"✅ Клиент создан\n\n"
        f"<b>{client.display_name}</b> · TG <code>{client.telegram_id}</code>\n"
        f"Бот: <code>{client.bot_instance}</code> · режим: {client.mode_default}"
    )


def admin_report(rep: dict) -> str:
    period_label = {
        "day": "за сутки",
        "week": "за неделю",
        "month": "за месяц",
        "all": "за всё время",
    }.get(rep["period"], rep["period"])

    if not rep["by_client"]:
        return f"📊 <b>Отчёт {period_label}</b>\n\n<i>Нет активных клиентов.</i>"

    lines = [f"📊 <b>Отчёт {period_label}</b>\n"]
    rows = []
    rows.append(f"{'Клиент':<22} {'+₽':>9} {'−₽':>9} {'Видео':>6} {'Баланс':>9}")
    rows.append("─" * 58)
    for c in rep["by_client"]:
        name = (c["display_name"] or "—")[:22]
        rows.append(
            f"{name:<22} "
            f"{str(int(c['topups'])):>9} "
            f"{str(int(c['charges'])):>9} "
            f"{c['videos']:>6} "
            f"{str(int(c['balance'])):>9}"
        )
    rows.append("─" * 58)
    t = rep["totals"]
    rows.append(
        f"{'ИТОГО':<22} "
        f"{str(int(t['topups'])):>9} "
        f"{str(int(t['charges'])):>9} "
        f"{t['videos']:>6} "
        f"{str(int(t['balance'])):>9}"
    )
    lines.append("<pre>" + "\n".join(rows) + "</pre>")
    return "\n".join(lines)


def admin_notify_topup_request(client: Client, amount: Decimal, balance: Decimal) -> str:
    """Уведомление админу когда клиент нажал 'Запросить счёт'."""
    uname = f"@{client.username}" if client.username else f"id:{client.telegram_id}"
    return (
        f"💳 <b>Запрос на пополнение</b>\n\n"
        f"Клиент: <b>{client.display_name}</b> · {uname}\n"
        f"Бот: <code>{client.bot_instance}</code>\n"
        f"Запрошенная сумма: <b>{fmt_rub(amount)}</b>\n"
        f"Текущий баланс: {fmt_rub(balance)}\n\n"
        f"Выставите счёт от ИП Панфёров. После оплаты нажмите «Пополнить»."
    )


def admin_access_denied() -> str:
    return "🚫 Доступ только для администраторов."


def cancelled() -> str:
    return "Отменено."
