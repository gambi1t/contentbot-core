"""
billing.db — SQLite schema + connection management.

Три таблицы:
    clients       — клиенты (по одному на telegram_id)
    balance_ops   — все операции баланса (пополнения И списания в одной таблице)
    videos        — ролики (для защиты от двойного списания)

SQLite настроен в WAL-режиме для параллельных читателей/писателей.
Все функции синхронные — в async-коде оборачивать в asyncio.to_thread(...).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Путь к БД. Можно переопределить через env для шаринга между инстансами ботов.
DB_PATH = Path(os.getenv("BILLING_DB_PATH", Path(__file__).parent / "billing.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL UNIQUE,
    username        TEXT,
    display_name    TEXT NOT NULL,
    bot_instance    TEXT NOT NULL,
    mode_default    TEXT NOT NULL DEFAULT 'self',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_clients_tg        ON clients(telegram_id);
CREATE INDEX IF NOT EXISTS idx_clients_instance  ON clients(bot_instance);

CREATE TABLE IF NOT EXISTS balance_ops (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL,
    type            TEXT NOT NULL,   -- 'topup' | 'charge' | 'refund' | 'manual_adjust'
    amount_rub      REAL NOT NULL,   -- +пополнения / -списания (в рублях, до 2 знаков)
    comment         TEXT,
    video_id        TEXT,            -- для charges/refund: привязка к ролику
    mode            TEXT,            -- 'self' | 'full' для charges
    trigger         TEXT,            -- 'crosspost' | 'download_final' | 'download_zip'
    admin_id        INTEGER,         -- telegram_id того кто провёл операцию
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE INDEX IF NOT EXISTS idx_ops_client   ON balance_ops(client_id);
CREATE INDEX IF NOT EXISTS idx_ops_video    ON balance_ops(video_id);
CREATE INDEX IF NOT EXISTS idx_ops_created  ON balance_ops(created_at);

CREATE TABLE IF NOT EXISTS videos (
    id              TEXT PRIMARY KEY,   -- = notion page id (или другой уникальный id ролика)
    client_id       INTEGER NOT NULL,
    mode            TEXT NOT NULL,       -- 'self' | 'full' на момент создания
    title           TEXT,                -- человеко-читаемое название (для истории)
    charged         INTEGER NOT NULL DEFAULT 0,  -- 0/1 — защита от двойного списания
    charged_at      TEXT,
    charged_trigger TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE INDEX IF NOT EXISTS idx_videos_client  ON videos(client_id);
CREATE INDEX IF NOT EXISTS idx_videos_charged ON videos(charged);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Контекстный менеджер: открывает соединение с WAL + Row factory, коммитит на выходе."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30.0,                 # ожидание снятия блокировки (сек)
        isolation_level=None,         # autocommit режим — BEGIN/COMMIT управляем вручную
    )
    conn.row_factory = sqlite3.Row
    try:
        # WAL даёт параллельных читателей и одного писателя без блокировок
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")  # компромисс скорости/надёжности
        yield conn
    finally:
        conn.close()


def init() -> None:
    """Создаёт таблицы, если их нет. Идемпотентно — безопасно вызывать при каждом старте бота."""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


def _wipe_all() -> None:
    """ТОЛЬКО для тестов. Удаляет все таблицы."""
    with get_conn() as conn:
        conn.executescript(
            "DROP TABLE IF EXISTS videos;"
            "DROP TABLE IF EXISTS balance_ops;"
            "DROP TABLE IF EXISTS clients;"
        )
