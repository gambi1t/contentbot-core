"""SQLite-backed store — the operational source of truth.

Why SQLite (not JSON, not Notion): transactions, optimistic concurrency, safe
across concurrent callbacks / Mini-App requests, survives restart, supports many
runs per card. Notion is only a mirror (see :meth:`record_notion_sync`).

The key primitive is :meth:`cas_transition` — a compare-and-swap on
``(stage, stage_version)``. It is BOTH the stage-advance mechanism and the
stale-button / concurrency guard: an action carrying an out-of-date
``stage_version`` simply doesn't match and changes nothing.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import (
    Run,
    Artifact,
    GATE_NONE,
    ST_RUNNING_JOB,
)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id() -> str:
    return uuid.uuid4().hex


class PipelineStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # WAL + busy_timeout matter for the real file db (concurrent callbacks /
        # future Mini App requests); harmless for :memory:.
        if db_path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Lightweight forward migrations for existing db files (no ORM)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(pipeline_runs)")}
        if "chat_id" not in cols:
            self.conn.execute("ALTER TABLE pipeline_runs ADD COLUMN chat_id TEXT")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── runs ────────────────────────────────────────────────────────────────
    def create_run(
        self,
        *,
        tenant: str,
        owner_user_id: str,
        plan: str,
        stage: str,
        status: str,
        actor_user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        notion_page_id: Optional[str] = None,
    ) -> Run:
        run_id = _new_run_id()
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO pipeline_runs
               (run_id, notion_page_id, tenant, owner_user_id, actor_user_id,
                chat_id, plan, stage, status, stage_version, active, paid_gate,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, notion_page_id, tenant, owner_user_id, actor_user_id,
             chat_id, plan, stage, status, 1, 1, GATE_NONE, now, now),
        )
        self.conn.commit()
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> Optional[Run]:
        row = self.conn.execute(
            "SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return _row_to_run(row) if row else None

    def get_active_runs(self, owner_user_id: str) -> list[Run]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_runs WHERE owner_user_id=? AND active=1 "
            "ORDER BY updated_at DESC",
            (owner_user_id,),
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_runs_awaiting_job(self) -> list[Run]:
        """Runs with a submitted provider job still rendering — for the poller."""
        rows = self.conn.execute(
            "SELECT * FROM pipeline_runs WHERE active=1 AND status=? "
            "AND current_job_id IS NOT NULL ORDER BY updated_at",
            (ST_RUNNING_JOB,),
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def cas_transition(
        self,
        run_id: str,
        *,
        expect_stage: str,
        expect_version: int,
        new_stage: str,
        new_status: str,
        set_paid_gate: Optional[str] = None,
        expect_paid_gate: Optional[str] = None,
        set_active: Optional[int] = None,
    ) -> bool:
        """Atomic compare-and-swap. Returns True iff exactly one row changed.

        Bumps ``stage_version`` on success — so a second click carrying the old
        version no longer matches (this is what makes paid-confirm idempotent
        and stale buttons inert).
        """
        sets = ["stage=?", "status=?", "stage_version=stage_version+1", "updated_at=?"]
        params: list = [new_stage, new_status, _utcnow()]
        if set_paid_gate is not None:
            sets.insert(2, "paid_gate=?")
            params.insert(2, set_paid_gate)
        if set_active is not None:
            sets.append("active=?")
            params.append(set_active)

        where = ["run_id=?", "stage=?", "stage_version=?", "active=1"]
        params += [run_id, expect_stage, expect_version]
        if expect_paid_gate is not None:
            where.append("paid_gate=?")
            params.append(expect_paid_gate)

        cur = self.conn.execute(
            f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE {' AND '.join(where)}",
            params,
        )
        self.conn.commit()
        return cur.rowcount == 1

    def set_status(self, run_id: str, status: str) -> None:
        """Status change WITHOUT a version bump (e.g. running_job → waiting_user
        after a step finishes — not a user action, must not invalidate buttons)."""
        self.conn.execute(
            "UPDATE pipeline_runs SET status=?, updated_at=? WHERE run_id=?",
            (status, _utcnow(), run_id),
        )
        self.conn.commit()

    def set_current_job(self, run_id: str, job_id: str, paid_gate: str) -> None:
        self.conn.execute(
            "UPDATE pipeline_runs SET current_job_id=?, paid_gate=?, updated_at=? WHERE run_id=?",
            (job_id, paid_gate, _utcnow(), run_id),
        )
        self.conn.commit()

    def record_notion_sync(self, run_id: str, ok: bool, notion_status: Optional[str] = None) -> None:
        """Best-effort Notion mirror result. On failure flag for later resync;
        a cron is deliberately deferred — `sync_notion_pending` script handles it."""
        if ok:
            self.conn.execute(
                "UPDATE pipeline_runs SET notion_sync_pending=0, notion_synced_at=?, "
                "notion_status=COALESCE(?, notion_status), updated_at=? WHERE run_id=?",
                (_utcnow(), notion_status, _utcnow(), run_id),
            )
        else:
            self.conn.execute(
                "UPDATE pipeline_runs SET notion_sync_pending=1, updated_at=? WHERE run_id=?",
                (_utcnow(), run_id),
            )
        self.conn.commit()

    # ── artifacts ─────────────────────────────────────────────────────────────
    def add_artifact(
        self,
        run_id: str,
        kind: str,
        *,
        path: Optional[str] = None,
        url: Optional[str] = None,
        text_content: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO pipeline_artifacts
               (run_id, kind, path, url, text_content, meta_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, kind, path, url, text_content,
             json.dumps(meta or {}, ensure_ascii=False), _utcnow()),
        )
        self.conn.commit()

    def get_artifacts(self, run_id: str) -> list[Artifact]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_artifacts WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [_row_to_artifact(r) for r in rows]

    # ── events (audit log) ─────────────────────────────────────────────────────
    def add_event(
        self,
        run_id: str,
        event_type: str,
        *,
        from_stage: Optional[str] = None,
        to_stage: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO pipeline_events
               (run_id, event_type, from_stage, to_stage, actor_user_id, payload_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, event_type, from_stage, to_stage, actor_user_id,
             json.dumps(payload or {}, ensure_ascii=False), _utcnow()),
        )
        self.conn.commit()

    def get_events(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_events WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        run_id=row["run_id"],
        tenant=row["tenant"],
        owner_user_id=row["owner_user_id"],
        plan=row["plan"],
        stage=row["stage"],
        status=row["status"],
        stage_version=row["stage_version"],
        active=row["active"],
        paid_gate=row["paid_gate"],
        actor_user_id=row["actor_user_id"],
        chat_id=row["chat_id"],
        notion_page_id=row["notion_page_id"],
        current_job_id=row["current_job_id"],
        notion_status=row["notion_status"],
        notion_synced_at=row["notion_synced_at"],
        notion_sync_pending=row["notion_sync_pending"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    meta = {}
    if row["meta_json"]:
        try:
            meta = json.loads(row["meta_json"])
        except (ValueError, TypeError):
            meta = {}
    return Artifact(
        id=row["id"],
        run_id=row["run_id"],
        kind=row["kind"],
        path=row["path"],
        url=row["url"],
        text_content=row["text_content"],
        meta=meta,
        created_at=row["created_at"],
    )
