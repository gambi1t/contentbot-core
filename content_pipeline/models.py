"""Data models + constants for the pipeline core.

Pure dataclasses, stdlib only. No Telegram, no bot.py, no tenant constants.
The core emits two output streams:
  * ``UIIntent``      — *what to show* (transport-agnostic; a Telegram adapter
    or a future Mini App adapter decides send vs edit vs screen).
  * ``EffectCommand`` — *what to actually do* (incl. paid provider jobs), each
    carrying an ``idempotency_key`` so re-delivery never double-charges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ── Stages (avatar plan) ────────────────────────────────────────────────────
STAGE_SCRIPT = "script"
STAGE_COVER = "cover"
STAGE_VOICE = "voice"
STAGE_AVATAR = "avatar"
STAGE_DONE = "done"

# ── Run statuses ────────────────────────────────────────────────────────────
ST_RUNNING_JOB = "running_job"
ST_WAITING_USER = "waiting_user"
ST_WAITING_INPUT = "waiting_input"
ST_WAITING_CONFIRM = "waiting_confirm"
ST_COMPLETED = "completed"
ST_CANCELLED = "cancelled"
ST_FAILED = "failed"

# ── paid_gate states ────────────────────────────────────────────────────────
GATE_NONE = "none"
GATE_PENDING = "pending"
GATE_CONFIRMED = "confirmed"
GATE_SPENT = "spent"

# ── Inbound event kinds ─────────────────────────────────────────────────────
EV_IDEA_RECEIVED = "idea_received"
EV_APPROVE = "approve"
EV_SKIP = "skip"
EV_UPLOAD_VOICE = "upload_voice"
EV_CONFIRM_PAID = "confirm_paid"
EV_OPEN_MATERIALS = "open_materials"
EV_RESUME = "resume"
# internal follow-up (emitted by the effect executor):
EV_STEP_COMPLETED = "step_completed"
# async provider-job outcomes (fed back by the poller, 1c):
EV_JOB_COMPLETED = "job_completed"
EV_JOB_FAILED = "job_failed"

# ── UIIntent kinds (transport-agnostic) ─────────────────────────────────────
UI_SHOW_STEP = "show_step"
UI_SHOW_RESUME_LIST = "show_resume_list"
UI_REQUEST_INPUT = "request_input"
UI_SHOW_COST_GATE = "show_cost_gate"
UI_SHOW_STATUS = "show_status"
UI_SHOW_MATERIALS = "show_materials"
UI_SHOW_STALE_STATE = "show_stale_state"
UI_SHOW_ERROR = "show_error"
UI_SHOW_RESULT = "show_result"  # deliver the finished video (1c)

# ── EffectCommand kinds (side effects; some cost money) ──────────────────────
EFF_GENERATE_SCRIPT = "generate_script"
EFF_GENERATE_COVER = "generate_cover_options"
EFF_START_PAID_JOB = "start_paid_provider_job"
EFF_UPDATE_NOTION = "update_notion_status"
EFF_BUILD_MATERIALS = "build_materials_zip"


@dataclass
class PipelineEvent:
    """An inbound event the spine reacts to. Transport-agnostic."""
    kind: str
    tenant: str = ""
    owner_user_id: str = ""
    actor_user_id: str = ""
    chat_id: str = ""
    run_id: Optional[str] = None
    stage: Optional[str] = None
    stage_version: Optional[int] = None
    notion_page_id: Optional[str] = None
    payload: dict = field(default_factory=dict)


@dataclass
class UIAction:
    """A button/action intent — NOT a Telegram callback. The adapter renders it.

    Carries ``stage`` + ``stage_version`` so the resulting event can be
    compare-and-swap'd against the run (stale-button guard).
    """
    label: str
    action: str               # approve | skip | upload | confirm_paid | open_materials | cancel | open_run
    run_id: str
    stage: str = ""
    stage_version: int = 0
    style: str = "default"    # default | primary | paid | danger | secondary


@dataclass
class InputField:
    """Requested input (e.g. a voice note upload)."""
    name: str
    kind: str                 # voice | text | photo
    prompt: str = ""


@dataclass
class UIIntent:
    kind: str
    run_id: str = ""
    title: str = ""
    body: str = ""
    actions: list[UIAction] = field(default_factory=list)
    fields: list[InputField] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class EffectCommand:
    kind: str
    run_id: str
    payload: dict = field(default_factory=dict)
    idempotency_key: str = ""


@dataclass
class Decision:
    """What the spine decided for one event: things to show + things to do."""
    intents: list[UIIntent] = field(default_factory=list)
    effects: list[EffectCommand] = field(default_factory=list)


@dataclass
class Run:
    run_id: str
    tenant: str
    owner_user_id: str
    plan: str
    stage: str
    status: str
    stage_version: int = 1
    active: int = 1
    paid_gate: str = GATE_NONE
    actor_user_id: Optional[str] = None
    chat_id: Optional[str] = None
    notion_page_id: Optional[str] = None
    current_job_id: Optional[str] = None
    job_started_at: Optional[str] = None
    notion_status: Optional[str] = None
    notion_synced_at: Optional[str] = None
    notion_sync_pending: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Artifact:
    run_id: str
    kind: str
    path: Optional[str] = None
    url: Optional[str] = None
    text_content: Optional[str] = None
    meta: dict = field(default_factory=dict)
    id: Optional[int] = None
    created_at: str = ""
