"""
billing.handlers — все Telegram-хэндлеры биллинга.

Регистрация в bot.py:
    from billing import handlers as billing_handlers
    billing_handlers.register(application)

Что регистрируется:
    CommandHandler  /billing             — главное меню клиента (если зарегистрирован)
    CommandHandler  /admin               — админ-меню (только ADMIN_TELEGRAM_IDS)
    CallbackQuery   c:*                  — все клиентские кнопки
    CallbackQuery   a:*                  — все админские кнопки
    ConversationHandler (×4)             — многошаговые флоу ввода текста

ВАЖНО: /start не регистрируется — это дело интегратора. В `bot.py`
при своём /start нужно вызвать billing_handlers.show_client_menu(update, context).
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from billing import api, keyboards, texts
from billing.config import ADMIN_TELEGRAM_IDS, BOT_INSTANCE, SUPPORT_CONTACT, is_admin

log = logging.getLogger("billing.handlers")


# ─── states for ConversationHandlers ──────────────────────────────────────

# клиент
CLIENT_REQ_AMOUNT = 100

# админ: пополнить
ADM_TOPUP_AMOUNT = 201

# админ: корректировка
ADM_ADJ_AMOUNT = 301
ADM_ADJ_COMMENT = 302

# админ: добавить клиента
ADM_NEW_TG = 401
ADM_NEW_NAME = 402
ADM_NEW_INSTANCE = 403
ADM_NEW_MODE = 404


# ─── helpers ──────────────────────────────────────────────────────────────

async def _edit(update: Update, text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> None:
    """Редактировать сообщение (для callback queries) или отправить новое."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
        except Exception as e:
            # сообщение могло быть удалено или слишком старое — просто пришлём новое
            log.debug("edit_message_text failed, sending new: %s", e)
    await update.effective_chat.send_message(
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _safe_answer(update: Update, text: str = "", show_alert: bool = False) -> None:
    """Ответить на callback_query, не падая если его нет."""
    if update.callback_query:
        try:
            await update.callback_query.answer(text=text or None, show_alert=show_alert)
        except Exception:
            pass


def _user_id(update: Update) -> int:
    return update.effective_user.id


async def _get_client_async(tg_id: int):
    return await asyncio.to_thread(api.get_client, tg_id)


async def _get_balance_async(tg_id: int) -> Decimal:
    return await asyncio.to_thread(api.get_balance, tg_id)


# ─── CLIENT: главное меню / /billing ──────────────────────────────────────

async def show_client_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Показать главное меню клиента. Вызывается из /billing или из bot.py после /start.
    Если клиент не зарегистрирован — показать «доступ ограничен».
    """
    tg_id = _user_id(update)
    client = await _get_client_async(tg_id)

    if not client or not client.is_active:
        await _edit(update, texts.client_unknown(tg_id))
        return

    balance = await _get_balance_async(tg_id)
    await _edit(
        update,
        texts.client_main_menu(client, balance),
        keyboards.client_main_inline(),
    )


async def cmd_billing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /billing — вход в меню клиента.

    Для админов команда не имеет смысла (админ не клиент), поэтому
    перебрасываем в /admin с понятным сообщением. Это избавляет от
    «Доступ ограничен» при случайном нажатии /billing владельцем бота.
    """
    tg_id = _user_id(update)
    if is_admin(tg_id):
        await update.effective_chat.send_message(
            "⚙️ Ты админ, а /billing — клиентская команда.\n\n"
            "Для управления клиентами и балансами используй /admin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("→ Открыть /admin", callback_data="a:menu")],
            ]),
        )
        return
    await show_client_menu(update, context)


# ─── CLIENT: callbacks ────────────────────────────────────────────────────

async def client_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_answer(update)
    await show_client_menu(update, context)


async def client_balance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_answer(update)
    tg_id = _user_id(update)
    client = await _get_client_async(tg_id)
    if not client:
        await _edit(update, texts.client_unknown(tg_id))
        return
    balance = await _get_balance_async(tg_id)
    history = await asyncio.to_thread(api.get_history, tg_id, 10)
    stats = await asyncio.to_thread(api.get_client_stats, tg_id)
    await _edit(
        update,
        texts.client_balance(client, balance, history, stats),
        keyboards.client_balance(),
    )


async def client_settings_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_answer(update)
    tg_id = _user_id(update)
    client = await _get_client_async(tg_id)
    if not client:
        await _edit(update, texts.client_unknown(tg_id))
        return
    await _edit(
        update,
        texts.client_settings(client),
        keyboards.client_settings(client.mode_default),
    )


async def client_set_mode_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback_data: c:set_mode:self | c:set_mode:full"""
    await _safe_answer(update)
    mode = update.callback_query.data.split(":")[2]
    tg_id = _user_id(update)
    client = await _get_client_async(tg_id)
    if not client:
        await _edit(update, texts.client_unknown(tg_id))
        return
    await asyncio.to_thread(api.set_client_mode, tg_id, mode)
    # обновим экран настроек
    client = await _get_client_async(tg_id)
    await _edit(
        update,
        texts.client_settings(client) + f"\n\n{texts.client_mode_changed(mode)}",
        keyboards.client_settings(client.mode_default),
    )


async def client_help_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_answer(update)
    await _edit(
        update,
        (
            "❓ <b>Помощь</b>\n\n"
            "Вы в личном кабинете клиента контент-завода.\n\n"
            "<b>Как это работает:</b>\n"
            "1. Пополняете баланс через счёт от ИП.\n"
            "2. Создаёте ролик — списание происходит в момент публикации "
            "или скачивания готового ролика.\n"
            "3. Сырые материалы (звук, аватар, b-roll) — бесплатно.\n\n"
            f"Вопросы: {SUPPORT_CONTACT}"
        ),
        keyboards.client_back_to_menu(),
    )


async def client_new_video_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «🎬 Новый ролик» из меню клиента."""
    await _safe_answer(update)
    tg_id = _user_id(update)
    client = await _get_client_async(tg_id)
    if not client:
        await _edit(update, texts.client_unknown(tg_id))
        return

    ok, reason, price = await asyncio.to_thread(api.can_create_video, tg_id, client.mode_default)
    if not ok and reason == "insufficient_balance":
        balance = await _get_balance_async(tg_id)
        await _edit(
            update,
            texts.client_insufficient(balance, client.mode_default),
            keyboards.client_insufficient(client.mode_default),
        )
        return

    # Здесь — делегирование основному пайплайну бота.
    # В bot.py интегратор вешает свой обработчик на callback_data 'c:start_pipeline'
    # либо мы просто возвращаем в меню с подсказкой.
    await _edit(
        update,
        (
            "🎬 <b>Создание ролика</b>\n\n"
            f"Режим: <b>{client.mode_default}</b> ({texts.fmt_rub(price)}/ролик)\n\n"
            "Пришлите голосовое / текст / ссылку — или нажмите на подходящий "
            "сценарий из Launch Monitor.\n\n"
            "<i>Оплата — только после публикации или скачивания готового ролика.</i>"
        ),
        keyboards.client_back_to_menu(),
    )


# ─── CLIENT conv: «запросить счёт» ────────────────────────────────────────

async def client_req_topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _safe_answer(update)
    tg_id = _user_id(update)
    client = await _get_client_async(tg_id)
    if not client:
        await _edit(update, texts.client_unknown(tg_id))
        return ConversationHandler.END
    await _edit(
        update,
        texts.client_request_topup_prompt(),
        keyboards.client_request_topup_cancel(),
    )
    return CLIENT_REQ_AMOUNT


async def client_req_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = Decimal(update.message.text.strip().replace(" ", "").replace(",", "."))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await update.message.reply_text(
            "⚠️ Не понял сумму. Введите число в рублях (например <code>12000</code>):",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.client_request_topup_cancel(),
        )
        return CLIENT_REQ_AMOUNT

    tg_id = _user_id(update)
    client = await _get_client_async(tg_id)
    balance = await _get_balance_async(tg_id)

    # уведомление клиенту
    await update.message.reply_text(
        texts.client_request_topup_sent(amount),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.client_back_to_menu(),
    )

    # уведомление всем админам
    for admin_id in ADMIN_TELEGRAM_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=texts.admin_notify_topup_request(client, amount, balance),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards.admin_topup_from_notification(tg_id),
            )
        except Exception as e:
            log.warning("failed to notify admin %s about topup request: %s", admin_id, e)

    return ConversationHandler.END


async def client_req_topup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _safe_answer(update)
    await show_client_menu(update, context)
    return ConversationHandler.END


# ─── ADMIN: menu / clients list ───────────────────────────────────────────

async def _gate_admin(update: Update) -> bool:
    """Проверка админских прав. Если не админ — показать denied и вернуть False."""
    if not is_admin(_user_id(update)):
        await _safe_answer(update, texts.admin_access_denied(), show_alert=True)
        await _edit(update, texts.admin_access_denied())
        return False
    return True


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate_admin(update):
        return
    await _edit(update, texts.admin_menu(), keyboards.admin_menu())


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_admin_menu(update, context)


async def admin_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_answer(update)
    await show_admin_menu(update, context)


async def admin_clients_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_answer(update)
    if not await _gate_admin(update):
        return
    clients = await asyncio.to_thread(api.list_clients, None, True)  # все боты, активные
    balances = {}
    for c in clients:
        balances[c.telegram_id] = await _get_balance_async(c.telegram_id)
    await _edit(
        update,
        texts.admin_clients_list(clients, balances),
        keyboards.admin_clients_list(clients),
    )


async def admin_client_card_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback_data: a:client:<tg_id>"""
    await _safe_answer(update)
    if not await _gate_admin(update):
        return
    tg_id = int(update.callback_query.data.split(":")[2])
    client = await _get_client_async(tg_id)
    if not client:
        await _edit(update, "Клиент не найден.", keyboards.admin_back_to_menu())
        return
    balance = await _get_balance_async(tg_id)
    history = await asyncio.to_thread(api.get_history, tg_id, 10)
    stats = await asyncio.to_thread(api.get_client_stats, tg_id)
    await _edit(
        update,
        texts.admin_client_card(client, balance, history, stats),
        keyboards.admin_client_card(client),
    )


async def admin_toggle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback_data: a:toggle:<tg_id>"""
    await _safe_answer(update)
    if not await _gate_admin(update):
        return
    tg_id = int(update.callback_query.data.split(":")[2])
    client = await _get_client_async(tg_id)
    if not client:
        return
    await asyncio.to_thread(api.set_client_active, tg_id, not client.is_active)
    # показываем обновлённую карточку
    update.callback_query.data = f"a:client:{tg_id}"
    await admin_client_card_cb(update, context)


async def admin_mode_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback_data: a:mode_menu:<tg_id>"""
    await _safe_answer(update)
    if not await _gate_admin(update):
        return
    tg_id = int(update.callback_query.data.split(":")[2])
    client = await _get_client_async(tg_id)
    if not client:
        return
    await _edit(
        update,
        f"⚙️ <b>Режим по умолчанию · {client.display_name}</b>\n\n"
        "Выберите режим — по нему будет идти списание за каждый новый ролик "
        "до тех пор, пока клиент сам не переключит в своих настройках.",
        keyboards.admin_mode_select(client),
    )


async def admin_set_mode_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback_data: a:set_mode:<tg_id>:<mode>"""
    await _safe_answer(update)
    if not await _gate_admin(update):
        return
    _, _, tg_id_s, mode = update.callback_query.data.split(":")
    tg_id = int(tg_id_s)
    await asyncio.to_thread(api.set_client_mode, tg_id, mode)
    update.callback_query.data = f"a:client:{tg_id}"
    await admin_client_card_cb(update, context)


async def admin_report_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_answer(update)
    if not await _gate_admin(update):
        return
    await _edit(
        update,
        "📊 <b>Отчёт по клиентам</b>\n\nВыберите период:",
        keyboards.admin_report_periods(),
    )


async def admin_report_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback_data: a:report:<period>"""
    await _safe_answer(update)
    if not await _gate_admin(update):
        return
    period = update.callback_query.data.split(":")[2]
    rep = await asyncio.to_thread(api.report, period, None)
    await _edit(update, texts.admin_report(rep), keyboards.admin_report_periods())


async def admin_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Общий отменщик — возвращает в админ-меню и закрывает conversation."""
    await _safe_answer(update, texts.cancelled())
    context.user_data.pop("billing_tmp", None)
    await show_admin_menu(update, context)
    return ConversationHandler.END


# ─── ADMIN conv: пополнить ────────────────────────────────────────────────

async def admin_topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: a:topup:<tg_id>"""
    await _safe_answer(update)
    if not await _gate_admin(update):
        return ConversationHandler.END
    tg_id = int(update.callback_query.data.split(":")[2])
    client = await _get_client_async(tg_id)
    if not client:
        await _edit(update, "Клиент не найден.", keyboards.admin_back_to_menu())
        return ConversationHandler.END
    context.user_data["billing_tmp"] = {"tg_id": tg_id}
    await _edit(
        update,
        texts.admin_topup_prompt(client),
        InlineKeyboardMarkup([[keyboards.admin_add_client_cancel().inline_keyboard[0][0]]]),
    )
    return ADM_TOPUP_AMOUNT


async def admin_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = Decimal(update.message.text.strip().replace(" ", "").replace(",", "."))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await update.message.reply_text(
            "⚠️ Некорректная сумма. Введите положительное число в рублях:",
            parse_mode=ParseMode.HTML,
        )
        return ADM_TOPUP_AMOUNT

    tmp = context.user_data.get("billing_tmp", {})
    tg_id = tmp.get("tg_id")
    client = await _get_client_async(tg_id)
    if not client:
        await update.message.reply_text("Клиент пропал из базы.")
        context.user_data.pop("billing_tmp", None)
        return ConversationHandler.END
    tmp["amount"] = str(amount)
    context.user_data["billing_tmp"] = tmp
    await update.message.reply_text(
        texts.admin_topup_confirm(client, amount),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.admin_topup_confirm(tg_id),
    )
    return ADM_TOPUP_AMOUNT  # остаёмся в этом state — дальше ждём callback a:topup_ok или a:cancel


async def admin_topup_ok_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: a:topup_ok:<tg_id>"""
    await _safe_answer(update)
    tmp = context.user_data.get("billing_tmp", {})
    tg_id_cb = int(update.callback_query.data.split(":")[2])
    tg_id = tmp.get("tg_id")
    amount_s = tmp.get("amount")

    if tg_id_cb != tg_id or not amount_s:
        await _edit(update, "Данные формы потеряны, попробуйте ещё раз.", keyboards.admin_back_to_menu())
        return ConversationHandler.END

    amount = Decimal(amount_s)
    client = await _get_client_async(tg_id)

    admin_id = _user_id(update)
    new_balance = await asyncio.to_thread(
        api.topup, tg_id, amount, admin_id, "Пополнение от админа"
    )

    # уведомление клиенту
    try:
        await context.bot.send_message(
            chat_id=tg_id,
            text=texts.client_topup_notification(amount, new_balance, "Пополнение от менеджера"),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.warning("failed to notify client %s about topup: %s", tg_id, e)

    await _edit(
        update,
        texts.admin_topup_done(client, amount, new_balance),
        keyboards.admin_client_card(client),
    )
    context.user_data.pop("billing_tmp", None)
    return ConversationHandler.END


# ─── ADMIN conv: корректировка ────────────────────────────────────────────

async def admin_adjust_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: a:adjust:<tg_id>"""
    await _safe_answer(update)
    if not await _gate_admin(update):
        return ConversationHandler.END
    tg_id = int(update.callback_query.data.split(":")[2])
    client = await _get_client_async(tg_id)
    if not client:
        return ConversationHandler.END
    context.user_data["billing_tmp"] = {"tg_id": tg_id}
    await _edit(
        update,
        texts.admin_adjust_amount_prompt(client),
        keyboards.admin_add_client_cancel(),
    )
    return ADM_ADJ_AMOUNT


async def admin_adjust_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = Decimal(update.message.text.strip().replace(" ", "").replace(",", "."))
        if amount == 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await update.message.reply_text(
            "⚠️ Введите ненулевое число. Положительное — начисление, отрицательное — списание."
        )
        return ADM_ADJ_AMOUNT
    tmp = context.user_data.get("billing_tmp", {})
    tmp["amount"] = str(amount)
    context.user_data["billing_tmp"] = tmp
    await update.message.reply_text(
        texts.admin_adjust_comment_prompt(amount),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.admin_add_client_cancel(),
    )
    return ADM_ADJ_COMMENT


async def admin_adjust_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    comment = update.message.text.strip()
    if not comment:
        await update.message.reply_text("⚠️ Комментарий обязателен. Введите причину корректировки:")
        return ADM_ADJ_COMMENT
    tmp = context.user_data.get("billing_tmp", {})
    tg_id = tmp.get("tg_id")
    amount = Decimal(tmp.get("amount"))
    tmp["comment"] = comment
    context.user_data["billing_tmp"] = tmp
    client = await _get_client_async(tg_id)
    await update.message.reply_text(
        texts.admin_adjust_confirm(client, amount, comment),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.admin_adjust_confirm(tg_id),
    )
    return ADM_ADJ_COMMENT  # ждём callback


async def admin_adjust_ok_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: a:adjust_ok:<tg_id>"""
    await _safe_answer(update)
    tmp = context.user_data.get("billing_tmp", {})
    tg_id_cb = int(update.callback_query.data.split(":")[2])
    tg_id = tmp.get("tg_id")
    if tg_id_cb != tg_id:
        await _edit(update, "Данные формы потеряны.", keyboards.admin_back_to_menu())
        return ConversationHandler.END
    amount = Decimal(tmp["amount"])
    comment = tmp["comment"]
    admin_id = _user_id(update)

    new_balance = await asyncio.to_thread(api.manual_adjust, tg_id, amount, admin_id, comment)
    client = await _get_client_async(tg_id)

    # уведомление клиенту — только если начисление
    if amount > 0:
        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=texts.client_topup_notification(amount, new_balance, comment),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            log.warning("failed to notify client about adjust: %s", e)

    await _edit(
        update,
        f"✅ Корректировка применена.\n\n{client.display_name}: {texts.fmt_rub(amount, signed=True)}\n"
        f"Новый баланс: <b>{texts.fmt_rub(new_balance)}</b>",
        keyboards.admin_client_card(client),
    )
    context.user_data.pop("billing_tmp", None)
    return ConversationHandler.END


# ─── ADMIN conv: добавить клиента ─────────────────────────────────────────

async def admin_add_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: a:add_client"""
    await _safe_answer(update)
    if not await _gate_admin(update):
        return ConversationHandler.END
    context.user_data["billing_tmp"] = {}
    await _edit(
        update,
        texts.admin_add_client_tg_id_prompt(),
        keyboards.admin_add_client_cancel(),
    )
    return ADM_NEW_TG


async def admin_add_client_tg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Возможные входы:
    #  1. Пересланное сообщение от клиента → msg.forward_from.id (most reliable)
    #  2. Число (ID напрямую)              → int(msg.text)
    #  3. @username или username           → подсказка что этого недостаточно
    #  4. Что-то ещё                       → общая подсказка
    msg = update.message
    tg_id = None
    username = None
    if msg.forward_from:
        tg_id = msg.forward_from.id
        username = msg.forward_from.username
    elif msg.forward_sender_name and not msg.forward_from:
        # Пользователь включил privacy для forwards — ID мы всё равно не увидим.
        await msg.reply_text(
            "⚠️ У этого клиента включена приватность пересылок — ID скрыт.\n\n"
            "Попроси его написать нашему боту <code>/start</code> — бот ответит "
            "ему сообщением, где будет его числовой ID. Пусть клиент перешлёт "
            "тебе это число — пришли его сюда.",
            parse_mode="HTML",
        )
        return ADM_NEW_TG
    else:
        raw = (msg.text or "").strip()
        if not raw:
            await msg.reply_text(
                "⚠️ Не понял. Пришли число (Telegram ID) или перешли любое "
                "сообщение от клиента."
            )
            return ADM_NEW_TG
        # Проверка на @username — частая ошибка
        if raw.startswith("@") or (raw and not raw.lstrip("-").isdigit() and not raw.isdigit()):
            await msg.reply_text(
                f"🤔 Похоже, ты прислал ник (<code>{raw}</code>), а мне нужен "
                f"<b>числовой ID</b>.\n\n"
                f"Telegram не разрешает получать ID по нику — это защита "
                f"пользователей. Самый быстрый способ:\n\n"
                f"👉 <b>Перешли сюда любое сообщение от {raw}</b> "
                f"(Forward, не копия) — я сам вытащу ID.\n\n"
                f"Или попроси клиента написать нашему боту <code>/start</code> — "
                f"бот покажет ему ID в ответе.",
                parse_mode="HTML",
            )
            return ADM_NEW_TG
        try:
            tg_id = int(raw)
        except (ValueError, TypeError):
            await msg.reply_text(
                "⚠️ Не похоже на число. Пришли Telegram ID (число) или "
                "перешли сюда сообщение от клиента."
            )
            return ADM_NEW_TG
    if not tg_id:
        await msg.reply_text("⚠️ Не удалось определить TG ID.")
        return ADM_NEW_TG

    tmp = context.user_data.get("billing_tmp", {})
    tmp["tg_id"] = tg_id
    tmp["username"] = username
    context.user_data["billing_tmp"] = tmp

    await msg.reply_text(
        texts.admin_add_client_name_prompt(tg_id),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.admin_add_client_cancel(),
    )
    return ADM_NEW_NAME


async def admin_add_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("⚠️ Имя не может быть пустым.")
        return ADM_NEW_NAME
    tmp = context.user_data.get("billing_tmp", {})
    tmp["name"] = name
    context.user_data["billing_tmp"] = tmp
    await update.message.reply_text(
        texts.admin_add_client_instance_prompt(name),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.admin_add_client_cancel(),
    )
    return ADM_NEW_INSTANCE


async def admin_add_client_instance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    instance = update.message.text.strip().lower()
    if not instance:
        await update.message.reply_text("⚠️ Код инстанса обязателен.")
        return ADM_NEW_INSTANCE
    tmp = context.user_data.get("billing_tmp", {})
    tmp["instance"] = instance
    context.user_data["billing_tmp"] = tmp
    await update.message.reply_text(
        "Выберите режим по умолчанию:",
        reply_markup=keyboards.admin_add_client_mode(),
    )
    return ADM_NEW_MODE


async def admin_add_client_mode_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: a:new_mode:<mode>"""
    await _safe_answer(update)
    mode = update.callback_query.data.split(":")[2]
    tmp = context.user_data.get("billing_tmp", {})
    tmp["mode"] = mode
    context.user_data["billing_tmp"] = tmp
    await _edit(
        update,
        texts.admin_add_client_confirm(tmp["tg_id"], tmp["name"], tmp["instance"], mode),
        keyboards.admin_add_client_confirm(),
    )
    return ADM_NEW_MODE


async def admin_add_client_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _safe_answer(update)
    tmp = context.user_data.get("billing_tmp", {})
    if not tmp:
        return ConversationHandler.END
    client = await asyncio.to_thread(
        api.register_client,
        telegram_id=tmp["tg_id"],
        username=tmp.get("username"),
        display_name=tmp["name"],
        bot_instance=tmp["instance"],
        mode_default=tmp["mode"],
    )
    await _edit(
        update,
        texts.admin_client_created(client),
        keyboards.admin_back_to_menu(),
    )
    context.user_data.pop("billing_tmp", None)
    return ConversationHandler.END


# ─── registration ─────────────────────────────────────────────────────────

def register(application: Application) -> None:
    """Зарегистрировать все хэндлеры биллинга в PTB Application."""
    # Команды
    application.add_handler(CommandHandler("billing", cmd_billing))
    application.add_handler(CommandHandler("admin", cmd_admin))

    # Клиентский conv: запросить счёт
    client_req_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(client_req_topup_start, pattern=r"^c:req_topup$")],
        states={
            CLIENT_REQ_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, client_req_topup_amount),
                CallbackQueryHandler(client_req_topup_cancel, pattern=r"^c:cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(client_req_topup_cancel, pattern=r"^c:cancel$")],
        per_chat=True,
        per_user=True,
    )
    application.add_handler(client_req_conv)

    # Админские conv'ы
    topup_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_topup_start, pattern=r"^a:topup:\d+$")],
        states={
            ADM_TOPUP_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_topup_amount),
                CallbackQueryHandler(admin_topup_ok_cb, pattern=r"^a:topup_ok:\d+$"),
                CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$")],
        per_chat=True,
        per_user=True,
    )
    application.add_handler(topup_conv)

    adjust_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_adjust_start, pattern=r"^a:adjust:\d+$")],
        states={
            ADM_ADJ_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_adjust_amount),
                CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$"),
            ],
            ADM_ADJ_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_adjust_comment),
                CallbackQueryHandler(admin_adjust_ok_cb, pattern=r"^a:adjust_ok:\d+$"),
                CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$")],
        per_chat=True,
        per_user=True,
    )
    application.add_handler(adjust_conv)

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_client_start, pattern=r"^a:add_client$")],
        states={
            ADM_NEW_TG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_client_tg),
                MessageHandler(filters.FORWARDED, admin_add_client_tg),
                CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$"),
            ],
            ADM_NEW_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_client_name),
                CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$"),
            ],
            ADM_NEW_INSTANCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_client_instance),
                CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$"),
            ],
            ADM_NEW_MODE: [
                CallbackQueryHandler(admin_add_client_mode_cb, pattern=r"^a:new_mode:(self|full)$"),
                CallbackQueryHandler(admin_add_client_confirm_cb, pattern=r"^a:new_confirm$"),
                CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(admin_cancel_cb, pattern=r"^a:cancel$")],
        per_chat=True,
        per_user=True,
    )
    application.add_handler(add_conv)

    # Клиентские callback'ы (не под conv)
    application.add_handler(CallbackQueryHandler(client_menu_cb, pattern=r"^c:menu$"))
    application.add_handler(CallbackQueryHandler(client_balance_cb, pattern=r"^c:balance$"))
    application.add_handler(CallbackQueryHandler(client_settings_cb, pattern=r"^c:settings$"))
    application.add_handler(CallbackQueryHandler(client_set_mode_cb, pattern=r"^c:set_mode:(self|full)$"))
    application.add_handler(CallbackQueryHandler(client_help_cb, pattern=r"^c:help$"))
    application.add_handler(CallbackQueryHandler(client_new_video_cb, pattern=r"^c:new_video$"))

    # Админские callback'ы (не под conv)
    application.add_handler(CallbackQueryHandler(admin_menu_cb, pattern=r"^a:menu$"))
    application.add_handler(CallbackQueryHandler(admin_clients_cb, pattern=r"^a:clients$"))
    application.add_handler(CallbackQueryHandler(admin_client_card_cb, pattern=r"^a:client:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_cb, pattern=r"^a:toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_mode_menu_cb, pattern=r"^a:mode_menu:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_set_mode_cb, pattern=r"^a:set_mode:\d+:(self|full)$"))
    application.add_handler(CallbackQueryHandler(admin_report_menu_cb, pattern=r"^a:report_menu$"))
    application.add_handler(CallbackQueryHandler(admin_report_cb, pattern=r"^a:report:(day|week|month|all)$"))

    log.info("billing handlers registered: admin_ids=%s, bot_instance=%s", ADMIN_TELEGRAM_IDS, BOT_INSTANCE)
