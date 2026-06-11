"""
billing.config — конфигурация через env.
"""
from __future__ import annotations

import os


def _parse_int_list(raw: str) -> list[int]:
    out = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk:
            try:
                out.append(int(chunk))
            except ValueError:
                pass
    return out


# Список Telegram ID админов (через запятую в .env)
# Пример: ADMIN_TELEGRAM_IDS=384671843,987654321
ADMIN_TELEGRAM_IDS: list[int] = _parse_int_list(os.getenv("ADMIN_TELEGRAM_IDS", ""))

# Куда слать клиентов, если что-то не так («свяжитесь с ...»)
SUPPORT_CONTACT: str = os.getenv("SUPPORT_CONTACT", "@postulataistudio")

# Имя инстанса бота (для отчётности, какой бот принял клиента).
# Пример: panferovai | lifedrive | shoestore
BOT_INSTANCE: str = os.getenv("BOT_INSTANCE", "panferovai")


def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_TELEGRAM_IDS
