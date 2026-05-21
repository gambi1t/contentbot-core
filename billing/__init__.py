"""
Billing module — pay-per-use счётчик + баланс для ботов контент-студии.

Подключение в bot.py:

    from billing import handlers as billing_handlers
    from billing import api as billing

    # при старте:
    billing.init()                                   # создаст billing.db если нет
    billing_handlers.register(application)           # зарегистрирует /billing, /admin и все кнопки

    # в коде пайплайна:
    ok, reason, price = billing.can_create_video(user_id)
    billing.register_video(notion_id, user_id, mode="self", title="...")
    billing.charge_video(notion_id, trigger="crosspost")    # идемпотентно

Конфиг — через .env:
    ADMIN_TELEGRAM_IDS=384671843
    SUPPORT_CONTACT=@postulataistudio
    BOT_INSTANCE=panferovai
    BILLING_DB_PATH=./billing/billing.db   # опционально
"""
from billing import api, config, db, handlers, keyboards, texts  # noqa: F401

__version__ = "0.1.0"
