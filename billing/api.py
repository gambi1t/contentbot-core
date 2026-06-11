"""
billing.api — публичное API модуля биллинга.

Все суммы — в рублях как Decimal. Копейки допустимы (для ручных корректировок).
Функции синхронные; в async-коде оборачивайте в asyncio.to_thread().

Основные сценарии:

1. Регистрация клиента администратором:
       billing.register_client(tg_id=..., username=..., display_name="Максим", bot_instance="lifedrive")

2. Проверка перед стартом ролика:
       ok, reason, price = billing.can_create_video(tg_id, mode="self")

3. Создание ролика (привязка к клиенту):
       billing.register_video(video_id="notion-page-id", telegram_id=tg_id, mode="self", title="Картинг")

4. Списание (идемпотентно):
       result = billing.charge_video(video_id, trigger="crosspost")
       # result.status in {'charged', 'already_charged', 'video_not_found', 'client_inactive'}

5. Пополнение:
       billing.topup(telegram_id=tg_id, amount_rub=12000, admin_id=admin_tg_id, comment="входной пакет")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from billing import db

# Цены по режимам — в рублях. Меняются на уровне продукта, не per-client.
PRICES_RUB: dict[str, Decimal] = {
    "self": Decimal("350"),       # клиент утверждает сам
    "full": Decimal("2500"),      # мы ведём за клиента
    "selfie": Decimal("150"),     # клиент сам записал говорящую голову; всегда self по UX
}

# Валидные значения полей
VALID_MODES = {"self", "full", "selfie"}
# Но как дефолт клиента селфи не может быть — это per-video режим
VALID_CLIENT_DEFAULT_MODES = {"self", "full"}
VALID_OP_TYPES = {"topup", "charge", "refund", "manual_adjust"}
VALID_TRIGGERS = {"crosspost", "download_final", "download_zip"}


# ─── DTO ─────────────────────────────────────────────────────────────────────

@dataclass
class Client:
    id: int
    telegram_id: int
    username: Optional[str]
    display_name: str
    bot_instance: str
    mode_default: str
    is_active: bool
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Client":
        return cls(
            id=row["id"],
            telegram_id=row["telegram_id"],
            username=row["username"],
            display_name=row["display_name"],
            bot_instance=row["bot_instance"],
            mode_default=row["mode_default"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )


@dataclass
class BalanceOp:
    id: int
    client_id: int
    type: str
    amount_rub: Decimal
    comment: Optional[str]
    video_id: Optional[str]
    mode: Optional[str]
    trigger: Optional[str]
    admin_id: Optional[int]
    created_at: str

    @classmethod
    def from_row(cls, row) -> "BalanceOp":
        return cls(
            id=row["id"],
            client_id=row["client_id"],
            type=row["type"],
            amount_rub=Decimal(str(row["amount_rub"])),
            comment=row["comment"],
            video_id=row["video_id"],
            mode=row["mode"],
            trigger=row["trigger"],
            admin_id=row["admin_id"],
            created_at=row["created_at"],
        )


@dataclass
class ChargeResult:
    status: str                    # 'charged' | 'already_charged' | 'video_not_found' | 'client_inactive' | 'insufficient_balance'
    video_id: str
    amount_rub: Optional[Decimal] = None
    new_balance: Optional[Decimal] = None
    message: str = ""


# ─── init ────────────────────────────────────────────────────────────────────

def init() -> None:
    """Инициализация БД. Вызывать при старте бота."""
    db.init()


# ─── clients ─────────────────────────────────────────────────────────────────

def register_client(
    telegram_id: int,
    display_name: str,
    bot_instance: str,
    username: Optional[str] = None,
    mode_default: str = "self",
) -> Client:
    """Создаёт клиента. Если уже существует — обновляет поля и возвращает существующего."""
    if mode_default not in VALID_CLIENT_DEFAULT_MODES:
        raise ValueError(
            f"mode_default must be one of {VALID_CLIENT_DEFAULT_MODES}, got {mode_default!r} "
            "(selfie не может быть дефолтом — это per-video режим)"
        )

    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()

        if existing:
            # Обновим username/display_name если изменились, но mode_default не трогаем
            conn.execute(
                "UPDATE clients SET username = ?, display_name = ?, bot_instance = ? "
                "WHERE telegram_id = ?",
                (username, display_name, bot_instance, telegram_id),
            )
            row = conn.execute(
                "SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            return Client.from_row(row)

        conn.execute(
            "INSERT INTO clients (telegram_id, username, display_name, bot_instance, mode_default) "
            "VALUES (?, ?, ?, ?, ?)",
            (telegram_id, username, display_name, bot_instance, mode_default),
        )
        row = conn.execute(
            "SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return Client.from_row(row)


def get_client(telegram_id: int) -> Optional[Client]:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return Client.from_row(row) if row else None


def list_clients(bot_instance: Optional[str] = None, active_only: bool = True) -> list[Client]:
    q = "SELECT * FROM clients WHERE 1=1"
    params: list = []
    if bot_instance:
        q += " AND bot_instance = ?"
        params.append(bot_instance)
    if active_only:
        q += " AND is_active = 1"
    q += " ORDER BY created_at DESC"
    with db.get_conn() as conn:
        return [Client.from_row(r) for r in conn.execute(q, params).fetchall()]


def set_client_mode(telegram_id: int, mode: str) -> None:
    if mode not in VALID_CLIENT_DEFAULT_MODES:
        raise ValueError(
            f"mode must be one of {VALID_CLIENT_DEFAULT_MODES}, got {mode!r} "
            "(selfie не может быть дефолтом)"
        )
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE clients SET mode_default = ? WHERE telegram_id = ?",
            (mode, telegram_id),
        )


def set_client_active(telegram_id: int, is_active: bool) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE clients SET is_active = ? WHERE telegram_id = ?",
            (1 if is_active else 0, telegram_id),
        )


# ─── balance / history ───────────────────────────────────────────────────────

def get_balance(telegram_id: int) -> Decimal:
    """Баланс = сумма всех balance_ops для клиента. Если клиент не найден — 0."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(o.amount_rub), 0) AS bal "
            "FROM balance_ops o JOIN clients c ON c.id = o.client_id "
            "WHERE c.telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return Decimal(str(row["bal"] if row else 0))


def get_history(telegram_id: int, limit: int = 10) -> list[BalanceOp]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT o.* FROM balance_ops o JOIN clients c ON c.id = o.client_id "
            "WHERE c.telegram_id = ? ORDER BY o.created_at DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()
        return [BalanceOp.from_row(r) for r in rows]


def get_client_stats(telegram_id: int) -> dict:
    """Сводка по клиенту: сколько пополнений, списаний, роликов."""
    with db.get_conn() as conn:
        client = conn.execute(
            "SELECT id FROM clients WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if not client:
            return {"topups_rub": Decimal(0), "charges_rub": Decimal(0), "videos_charged": 0}

        cid = client["id"]
        topups = conn.execute(
            "SELECT COALESCE(SUM(amount_rub), 0) AS s FROM balance_ops "
            "WHERE client_id = ? AND type = 'topup'",
            (cid,),
        ).fetchone()["s"]
        charges = conn.execute(
            "SELECT COALESCE(SUM(amount_rub), 0) AS s FROM balance_ops "
            "WHERE client_id = ? AND type = 'charge'",
            (cid,),
        ).fetchone()["s"]
        n_videos = conn.execute(
            "SELECT COUNT(*) AS n FROM videos WHERE client_id = ? AND charged = 1",
            (cid,),
        ).fetchone()["n"]

        return {
            "topups_rub": Decimal(str(topups)),
            "charges_rub": Decimal(str(abs(charges))),
            "videos_charged": n_videos,
        }


# ─── pre-check / video creation ──────────────────────────────────────────────

def can_create_video(telegram_id: int, mode: Optional[str] = None) -> tuple[bool, str, Decimal]:
    """
    Проверяет, хватит ли баланса на новый ролик в режиме mode.
    Если mode=None — берёт mode_default клиента.
    Возвращает (ok, reason, price_required).
    """
    client = get_client(telegram_id)
    if not client:
        return False, "client_not_registered", Decimal(0)
    if not client.is_active:
        return False, "client_inactive", Decimal(0)

    effective_mode = mode or client.mode_default
    if effective_mode not in VALID_MODES:
        return False, f"invalid_mode:{effective_mode}", Decimal(0)

    price = PRICES_RUB[effective_mode]
    balance = get_balance(telegram_id)

    if balance < price:
        return False, "insufficient_balance", price
    return True, "ok", price


def register_video(video_id: str, telegram_id: int, mode: str, title: str = "") -> None:
    """
    Привязывает ролик к клиенту. Сам по себе денег не списывает.
    Если ролик уже есть — обновляет title/mode (на случай смены режима до финализации).
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}")
    client = get_client(telegram_id)
    if not client:
        raise ValueError(f"client not registered: telegram_id={telegram_id}")

    with db.get_conn() as conn:
        existing = conn.execute("SELECT charged FROM videos WHERE id = ?", (video_id,)).fetchone()
        if existing:
            if existing["charged"]:
                # Уже списано — не трогаем mode (чтобы исторически было видно, по какому режиму списали)
                conn.execute("UPDATE videos SET title = ? WHERE id = ?", (title, video_id))
            else:
                conn.execute(
                    "UPDATE videos SET mode = ?, title = ? WHERE id = ?",
                    (mode, title, video_id),
                )
        else:
            conn.execute(
                "INSERT INTO videos (id, client_id, mode, title) VALUES (?, ?, ?, ?)",
                (video_id, client.id, mode, title),
            )


# ─── CHARGE (идемпотентное списание) ─────────────────────────────────────────

def charge_video(video_id: str, trigger: str) -> ChargeResult:
    """
    Идемпотентное списание за финальный ролик.
    Если уже списано — возвращает already_charged, деньги не трогает.
    Если видео не зарегистрировано — video_not_found.
    Если баланса не хватает — insufficient_balance (видео остаётся в долг, списания нет).
    """
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"trigger must be one of {VALID_TRIGGERS}")

    with db.get_conn() as conn:
        # Всю транзакцию держим под BEGIN IMMEDIATE, чтобы два параллельных
        # триггера (кросспост + скачать) не списали дважды.
        conn.execute("BEGIN IMMEDIATE")
        try:
            video = conn.execute(
                "SELECT v.*, c.telegram_id, c.is_active "
                "FROM videos v JOIN clients c ON c.id = v.client_id "
                "WHERE v.id = ?",
                (video_id,),
            ).fetchone()

            if not video:
                conn.execute("ROLLBACK")
                return ChargeResult(status="video_not_found", video_id=video_id,
                                    message="Ролик не зарегистрирован в биллинге.")

            if video["charged"]:
                conn.execute("ROLLBACK")
                return ChargeResult(
                    status="already_charged",
                    video_id=video_id,
                    message="Ролик уже оплачен ранее.",
                )

            if not video["is_active"]:
                conn.execute("ROLLBACK")
                return ChargeResult(status="client_inactive", video_id=video_id,
                                    message="Клиент деактивирован.")

            mode = video["mode"]
            price = PRICES_RUB[mode]

            # Считаем баланс внутри транзакции
            balance_row = conn.execute(
                "SELECT COALESCE(SUM(amount_rub), 0) AS bal FROM balance_ops WHERE client_id = ?",
                (video["client_id"],),
            ).fetchone()
            balance = Decimal(str(balance_row["bal"]))

            if balance < price:
                conn.execute("ROLLBACK")
                return ChargeResult(
                    status="insufficient_balance",
                    video_id=video_id,
                    amount_rub=price,
                    new_balance=balance,
                    message=f"Недостаточно средств: {balance} < {price}.",
                )

            # Списание
            conn.execute(
                "INSERT INTO balance_ops (client_id, type, amount_rub, comment, video_id, mode, trigger) "
                "VALUES (?, 'charge', ?, ?, ?, ?, ?)",
                (
                    video["client_id"],
                    float(-price),
                    (video["title"] or f"Ролик {video_id[:8]}"),
                    video_id,
                    mode,
                    trigger,
                ),
            )
            conn.execute(
                "UPDATE videos SET charged = 1, charged_at = datetime('now'), charged_trigger = ? "
                "WHERE id = ?",
                (trigger, video_id),
            )
            conn.execute("COMMIT")

            return ChargeResult(
                status="charged",
                video_id=video_id,
                amount_rub=price,
                new_balance=balance - price,
                message=f"Списано {price} ₽ за ролик.",
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ─── admin ops ───────────────────────────────────────────────────────────────

def topup(telegram_id: int, amount_rub: Decimal, admin_id: int, comment: str = "") -> Decimal:
    """Пополнение баланса админом. Возвращает новый баланс."""
    amount_rub = Decimal(str(amount_rub))
    if amount_rub <= 0:
        raise ValueError("amount_rub must be positive for topup")

    client = get_client(telegram_id)
    if not client:
        raise ValueError(f"client not registered: telegram_id={telegram_id}")

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO balance_ops (client_id, type, amount_rub, comment, admin_id) "
            "VALUES (?, 'topup', ?, ?, ?)",
            (client.id, float(amount_rub), comment or "Пополнение", admin_id),
        )
    return get_balance(telegram_id)


def manual_adjust(telegram_id: int, amount_rub: Decimal, admin_id: int, comment: str) -> Decimal:
    """Ручная корректировка (в плюс или минус). Требует комментарий."""
    amount_rub = Decimal(str(amount_rub))
    if not comment.strip():
        raise ValueError("comment is required for manual_adjust")

    client = get_client(telegram_id)
    if not client:
        raise ValueError(f"client not registered: telegram_id={telegram_id}")

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO balance_ops (client_id, type, amount_rub, comment, admin_id) "
            "VALUES (?, 'manual_adjust', ?, ?, ?)",
            (client.id, float(amount_rub), comment, admin_id),
        )
    return get_balance(telegram_id)


def refund_video(video_id: str, admin_id: int, comment: str = "") -> Decimal:
    """
    Возврат денег за ролик. Возвращает сумму, равную списанию; помечает видео как не оплаченное
    (чтобы его можно было потом снова списать, если нужно — или просто оставить как брак).
    """
    with db.get_conn() as conn:
        video = conn.execute(
            "SELECT v.*, c.telegram_id FROM videos v JOIN clients c ON c.id = v.client_id "
            "WHERE v.id = ?",
            (video_id,),
        ).fetchone()
        if not video:
            raise ValueError(f"video not found: {video_id}")
        if not video["charged"]:
            raise ValueError(f"video not charged, nothing to refund: {video_id}")

        price = PRICES_RUB[video["mode"]]

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO balance_ops (client_id, type, amount_rub, comment, video_id, mode, admin_id) "
                "VALUES (?, 'refund', ?, ?, ?, ?, ?)",
                (
                    video["client_id"],
                    float(price),
                    comment or "Возврат за брак",
                    video_id,
                    video["mode"],
                    admin_id,
                ),
            )
            conn.execute(
                "UPDATE videos SET charged = 0, charged_at = NULL, charged_trigger = NULL "
                "WHERE id = ?",
                (video_id,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return get_balance(video["telegram_id"])


# ─── reports ─────────────────────────────────────────────────────────────────

def report(period: str = "all", bot_instance: Optional[str] = None) -> dict:
    """
    Сводный отчёт для админки. period ∈ {'day', 'week', 'month', 'all'}.
    Возвращает dict с ключами:
        by_client: list of {display_name, username, topups, charges, videos, balance}
        totals:    {topups, charges, videos, balance}
    """
    now = datetime.utcnow()
    if period == "day":
        since = (now - timedelta(days=1)).isoformat()
    elif period == "week":
        since = (now - timedelta(days=7)).isoformat()
    elif period == "month":
        since = (now - timedelta(days=30)).isoformat()
    elif period == "all":
        since = "1970-01-01T00:00:00"
    else:
        raise ValueError(f"period must be one of {{'day','week','month','all'}}, got {period!r}")

    q_clients = "SELECT * FROM clients WHERE is_active = 1"
    params_c: list = []
    if bot_instance:
        q_clients += " AND bot_instance = ?"
        params_c.append(bot_instance)

    with db.get_conn() as conn:
        clients = conn.execute(q_clients, params_c).fetchall()
        by_client = []
        tot_topups = Decimal(0)
        tot_charges = Decimal(0)
        tot_videos = 0
        tot_balance = Decimal(0)

        for c in clients:
            topups = conn.execute(
                "SELECT COALESCE(SUM(amount_rub), 0) AS s FROM balance_ops "
                "WHERE client_id = ? AND type = 'topup' AND created_at >= ?",
                (c["id"], since),
            ).fetchone()["s"]
            charges = conn.execute(
                "SELECT COALESCE(SUM(amount_rub), 0) AS s FROM balance_ops "
                "WHERE client_id = ? AND type = 'charge' AND created_at >= ?",
                (c["id"], since),
            ).fetchone()["s"]
            videos = conn.execute(
                "SELECT COUNT(*) AS n FROM videos WHERE client_id = ? AND charged = 1 "
                "AND charged_at >= ?",
                (c["id"], since),
            ).fetchone()["n"]
            balance = conn.execute(
                "SELECT COALESCE(SUM(amount_rub), 0) AS s FROM balance_ops WHERE client_id = ?",
                (c["id"],),
            ).fetchone()["s"]

            topups_d = Decimal(str(topups))
            charges_d = Decimal(str(abs(charges)))
            balance_d = Decimal(str(balance))

            by_client.append({
                "telegram_id": c["telegram_id"],
                "username": c["username"],
                "display_name": c["display_name"],
                "bot_instance": c["bot_instance"],
                "topups": topups_d,
                "charges": charges_d,
                "videos": videos,
                "balance": balance_d,
            })
            tot_topups += topups_d
            tot_charges += charges_d
            tot_videos += videos
            tot_balance += balance_d

        return {
            "period": period,
            "bot_instance": bot_instance,
            "by_client": by_client,
            "totals": {
                "topups": tot_topups,
                "charges": tot_charges,
                "videos": tot_videos,
                "balance": tot_balance,
            },
        }
