"""
billing.keyboards — клавиатуры для всех экранов биллинга.

Стиль: гибрид Reply + Inline.
- ReplyKeyboardMarkup (персистентно снизу) — главные меню, навигация
- InlineKeyboardMarkup (под сообщением) — подтверждения форм, контекстные действия

Callback data префиксы для inline:
    c:*    — клиентские действия
    a:*    — админские действия
    noop   — заглушка

Текстовые метки reply-кнопок (используются также в handlers.py для роутинга):
    RB_* — reply-button labels
"""
from __future__ import annotations

from typing import Iterable

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from billing.api import Client


# ═══ Reply-button labels ═══════════════════════════════════════════════════
# ВАЖНО: эти строки используются handlers.py для роутинга входящих MessageHandler.
# Менять — только синхронно с handlers.py.

RB_NEW_VIDEO       = "🎬 Новый ролик"
RB_QUICK_IDEA      = "🎤 Быстрая идея"
RB_SELFIE          = "🎥 Селфи"
RB_CARDS           = "📥 Карточки"
RB_CARDS_ALL       = "📚 Все карточки"
RB_BALANCE         = "💰 Баланс"

# Админские reply-кнопки (у Артёма ещё под /admin)
RB_ADM_CLIENTS     = "👥 Клиенты"
RB_ADM_REPORT      = "📊 Отчёт"
RB_ADM_ADD_CLIENT  = "➕ Новый клиент"
RB_ADM_EXIT        = "◀ Выйти из админки"


# ═══ MAIN REPLY MENUS ══════════════════════════════════════════════════════

def ilan_main() -> ReplyKeyboardMarkup:
    """Главное меню @panferovai_contentbot (Артём). Без баланса."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(RB_NEW_VIDEO)],
            [KeyboardButton(RB_QUICK_IDEA), KeyboardButton(RB_SELFIE)],
            [KeyboardButton(RB_CARDS), KeyboardButton(RB_CARDS_ALL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def client_main() -> ReplyKeyboardMarkup:
    """Главное меню клиентских инстансов (Максим и далее). С кнопкой Баланс."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(RB_NEW_VIDEO)],
            [KeyboardButton(RB_QUICK_IDEA), KeyboardButton(RB_SELFIE)],
            [KeyboardButton(RB_CARDS), KeyboardButton(RB_CARDS_ALL)],
            [KeyboardButton(RB_BALANCE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_reply_menu() -> ReplyKeyboardMarkup:
    """Reply-меню админки (Артём вошёл через /admin)."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(RB_ADM_CLIENTS), KeyboardButton(RB_ADM_REPORT)],
            [KeyboardButton(RB_ADM_ADD_CLIENT)],
            [KeyboardButton(RB_ADM_EXIT)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def remove() -> ReplyKeyboardRemove:
    """Спрятать reply-клавиатуру."""
    return ReplyKeyboardRemove()


# ═══ CLIENT — INLINE (под сообщением, контекстные) ══════════════════════════

def client_balance() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Запросить счёт", callback_data="c:req_topup")],
    ])


def client_insufficient(current_mode: str) -> InlineKeyboardMarkup:
    """Экран «недостаточно средств» при попытке создать ролик."""
    rows = [
        [InlineKeyboardButton("💳 Запросить счёт", callback_data="c:req_topup")],
    ]
    if current_mode == "full":
        rows.append([InlineKeyboardButton("💡 На self-тариф (350 ₽)", callback_data="c:set_mode:self")])
    return InlineKeyboardMarkup(rows)


def client_request_topup_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отмена", callback_data="c:cancel")],
    ])


def selfie_path_choice() -> InlineKeyboardMarkup:
    """После загрузки видео для селфи — клиент выбирает путь сборки."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎤 Только субтитры", callback_data="c:selfie:plain")],
        [InlineKeyboardButton("🎬 + B-roll (бесплатно)", callback_data="c:selfie:broll")],
        [InlineKeyboardButton("❌ Отмена", callback_data="c:cancel")],
    ])


def selfie_done() -> InlineKeyboardMarkup:
    """После готового селфи — что дальше."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Опубликовать", callback_data="c:selfie:publish")],
        [InlineKeyboardButton("📥 Скачать", callback_data="c:selfie:download")],
        [InlineKeyboardButton("◀ В меню", callback_data="c:menu")],
    ])


# ═══ ADMIN — INLINE ═════════════════════════════════════════════════════════

def admin_menu() -> InlineKeyboardMarkup:
    """Главное inline-меню админа (под сообщением /admin).

    Добавлено 2026-04-21 — раньше handlers.py ссылался на эту функцию,
    но сама функция отсутствовала (пакет был полуфабрикатом). Из-за этого
    /admin падал с AttributeError в _edit(update, ..., keyboards.admin_menu()).
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Клиенты", callback_data="a:clients")],
        [InlineKeyboardButton("➕ Добавить клиента", callback_data="a:add_client")],
        [InlineKeyboardButton("📊 Отчёт", callback_data="a:report_menu")],
    ])


def client_main_inline() -> InlineKeyboardMarkup:
    """Главное inline-меню клиента (под сообщением /billing или /start).

    Раньше handlers.show_client_menu передавал сюда ``keyboards.client_main()``,
    но client_main — это ReplyKeyboardMarkup. В сочетании с parse_mode=HTML
    и edit_message_text это не работало корректно. Добавлено inline-дубль.
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Новый ролик", callback_data="c:new_video")],
        [InlineKeyboardButton("💰 Баланс / история", callback_data="c:balance")],
        [InlineKeyboardButton("💳 Запросить пополнение", callback_data="c:req_topup")],
        [InlineKeyboardButton("⚙️ Настройки (режим)", callback_data="c:settings")],
        [InlineKeyboardButton("❓ Помощь", callback_data="c:help")],
    ])


def admin_clients_list(clients: Iterable[Client]) -> InlineKeyboardMarkup:
    rows = []
    for c in clients:
        rows.append([InlineKeyboardButton(c.display_name, callback_data=f"a:client:{c.telegram_id}")])
    rows.append([InlineKeyboardButton("➕ Добавить клиента", callback_data="a:add_client")])
    return InlineKeyboardMarkup(rows)


def admin_client_card(client: Client) -> InlineKeyboardMarkup:
    toggle_label = "🚫 Деактивировать" if client.is_active else "✅ Активировать"
    rows = [
        [
            InlineKeyboardButton("➕ Пополнить", callback_data=f"a:topup:{client.telegram_id}"),
            InlineKeyboardButton("➖ Корректировка", callback_data=f"a:adjust:{client.telegram_id}"),
        ],
        [InlineKeyboardButton("⚙️ Сменить режим", callback_data=f"a:mode_menu:{client.telegram_id}")],
        [InlineKeyboardButton(toggle_label, callback_data=f"a:toggle:{client.telegram_id}")],
        [InlineKeyboardButton("◀ К списку клиентов", callback_data="a:clients")],
    ]
    return InlineKeyboardMarkup(rows)


def admin_mode_select(client: Client) -> InlineKeyboardMarkup:
    self_label = "🔘 self-service (350 ₽)" if client.mode_default == "self" else "○ self-service (350 ₽)"
    full_label = "🔘 full-service (2 500 ₽)" if client.mode_default == "full" else "○ full-service (2 500 ₽)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(self_label, callback_data=f"a:set_mode:{client.telegram_id}:self")],
        [InlineKeyboardButton(full_label, callback_data=f"a:set_mode:{client.telegram_id}:full")],
        [InlineKeyboardButton("◀ Назад", callback_data=f"a:client:{client.telegram_id}")],
    ])


def admin_topup_confirm(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"a:topup_ok:{tg_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data="a:cancel"),
        ],
    ])


def admin_adjust_confirm(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"a:adjust_ok:{tg_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data="a:cancel"),
        ],
    ])


def admin_report_periods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Сутки", callback_data="a:report:day"),
            InlineKeyboardButton("Неделя", callback_data="a:report:week"),
        ],
        [
            InlineKeyboardButton("Месяц", callback_data="a:report:month"),
            InlineKeyboardButton("Всё время", callback_data="a:report:all"),
        ],
        [InlineKeyboardButton("◀ В админку", callback_data="a:menu")],
    ])


def admin_add_client_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отмена", callback_data="a:cancel")],
    ])


def admin_add_client_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("self (350 ₽)", callback_data="a:new_mode:self"),
            InlineKeyboardButton("full (2 500 ₽)", callback_data="a:new_mode:full"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="a:cancel")],
    ])


def admin_add_client_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Создать", callback_data="a:new_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="a:cancel"),
        ],
    ])


def admin_topup_from_notification(tg_id: int) -> InlineKeyboardMarkup:
    """Кнопки в уведомлении админа при запросе счёта от клиента."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Пополнить сейчас", callback_data=f"a:topup:{tg_id}")],
        [InlineKeyboardButton("👤 Карточка клиента", callback_data=f"a:client:{tg_id}")],
    ])
