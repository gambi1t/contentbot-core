"""Telegram adapter for the pipeline spine — a HIDDEN, parallel entry point.

This is the bot-side translation layer:
  * Telegram update / callback  → ``PipelineEvent``
  * ``UIIntent``                → Telegram message + inline buttons
  * ``EffectCommand``           → executed via the injected ``BotStepRunner``

It runs ALONGSIDE the live bot (command ``/spine``), touching none of the
existing handlers. ``content_pipeline`` stays pure: this module imports it, not
the other way round. ``telegram`` is imported lazily inside handlers so the pure
codec/keyboard helpers (and their unit tests) work without telegram installed.

Slice 1b scope: idea → script → cover → voice(button stub) → avatar cost-gate.
No real provider call (the gate is the finish line; ``start_paid_job`` raises).
Real voice-message intake and the HeyGen call are 1c.
"""
from __future__ import annotations

import logging

from content_pipeline.core import PipelineSpine, EffectExecutor, drive
from content_pipeline.models import (
    PipelineEvent,
    UIIntent, UIAction,
    EV_IDEA_RECEIVED, EV_APPROVE, EV_SKIP, EV_UPLOAD_VOICE, EV_CONFIRM_PAID,
    EV_OPEN_MATERIALS, EV_RESUME,
)
from content_pipeline.store import PipelineStore
from pipeline_step_services import BotStepRunner

logger = logging.getLogger("pipeline_adapter")

CB_PREFIX = "sp"

# Compact action codes — keep callback_data well under Telegram's 64-byte limit.
_ACTION_TO_CODE = {
    "approve": "a",
    "skip": "s",
    "upload": "u",
    "confirm_paid": "p",
    "open_materials": "m",
    "open_run": "o",
    "cancel": "c",
}
_CODE_TO_ACTION = {v: k for k, v in _ACTION_TO_CODE.items()}

# action → the PipelineEvent kind it produces
_ACTION_TO_EVENT = {
    "approve": EV_APPROVE,
    "skip": EV_SKIP,
    "upload": EV_UPLOAD_VOICE,
    "confirm_paid": EV_CONFIRM_PAID,
    "open_materials": EV_OPEN_MATERIALS,
    "open_run": EV_OPEN_MATERIALS,  # 1b: re-show current step's materials/status
}


# ── pure helpers (telegram-free → unit-testable) ────────────────────────────
def encode_action(a: UIAction) -> str:
    """UIAction → callback_data string ``sp:run:stage:ver:code``."""
    code = _ACTION_TO_CODE.get(a.action, "c")
    return f"{CB_PREFIX}:{a.run_id}:{a.stage}:{a.stage_version}:{code}"


def decode_cb(data: str) -> dict:
    """callback_data → {action, run_id, stage, stage_version}. Raises ValueError."""
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != CB_PREFIX:
        raise ValueError(f"bad spine callback: {data!r}")
    _, run_id, stage, ver, code = parts
    return {
        "action": _CODE_TO_ACTION.get(code, "cancel"),
        "run_id": run_id,
        "stage": stage,
        "stage_version": int(ver),
    }


def intent_to_keyboard_spec(intent: UIIntent) -> list[list[tuple[str, str]]]:
    """UIIntent → rows of (label, callback_data). Empty if no actions."""
    return [[(a.label, encode_action(a))] for a in intent.actions]


def intent_text(intent: UIIntent) -> str:
    head = (intent.title + "\n\n") if intent.title else ""
    return f"{head}{intent.body}".strip() or "…"


# ── module singletons (built by register_pipeline_spine) ────────────────────
_SPINE: PipelineSpine | None = None
_STORE: PipelineStore | None = None
_EXECUTOR: EffectExecutor | None = None


def _ready() -> bool:
    return _SPINE is not None and _EXECUTOR is not None


async def _render(bot, chat_id, intents) -> None:
    """Send each UIIntent as its own Telegram message (+ inline buttons)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # lazy
    for intent in intents:
        spec = intent_to_keyboard_spec(intent)
        markup = None
        if spec:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(lbl, callback_data=cb) for (lbl, cb) in row]
                 for row in spec]
            )
        await bot.send_message(chat_id=chat_id, text=intent_text(intent), reply_markup=markup)


# ── handlers ────────────────────────────────────────────────────────────────
async def cmd_spine(update, context) -> None:
    """``/spine <идея>`` — start a new pipeline run on the parallel track."""
    if not _ready():
        await update.message.reply_text("Спайн ещё не инициализирован.")
        return
    idea = " ".join(context.args).strip() if getattr(context, "args", None) else ""
    if not idea:
        await update.message.reply_text(
            "Использование: /spine <идея ролика>\n"
            "(экспериментальный конвейер; на живой пайплайн не влияет)")
        return
    user_id = str(update.effective_user.id)
    ev = PipelineEvent(
        kind=EV_IDEA_RECEIVED, tenant="maksim",
        owner_user_id=user_id, actor_user_id=user_id,
        chat_id=str(update.effective_chat.id),
        payload={"idea_text": idea},
    )
    try:
        res = drive(_SPINE, _EXECUTOR, ev)
    except Exception as e:  # never crash the live bot from the experimental track
        logger.error(f"[spine] cmd_spine drive failed: {e}", exc_info=True)
        await update.message.reply_text(f"Спайн: ошибка — {e}")
        return
    await _render(update.get_bot(), update.effective_chat.id, res.intents)


async def cmd_spine_resume(update, context) -> None:
    """``/spine_resume`` — list active runs and offer to continue."""
    if not _ready():
        await update.message.reply_text("Спайн ещё не инициализирован.")
        return
    user_id = str(update.effective_user.id)
    ev = PipelineEvent(kind=EV_RESUME, owner_user_id=user_id, actor_user_id=user_id,
                       chat_id=str(update.effective_chat.id))
    res = drive(_SPINE, _EXECUTOR, ev)
    await _render(update.get_bot(), update.effective_chat.id, res.intents)


async def on_spine_callback(update, context) -> None:
    """Inline-button presses with the ``sp:`` prefix."""
    query = update.callback_query
    await query.answer()
    try:
        parsed = decode_cb(query.data)
    except ValueError:
        return
    user_id = str(update.effective_user.id)
    ev = PipelineEvent(
        kind=_ACTION_TO_EVENT.get(parsed["action"], EV_OPEN_MATERIALS),
        tenant="maksim", owner_user_id=user_id, actor_user_id=user_id,
        chat_id=str(query.message.chat_id),
        run_id=parsed["run_id"], stage=parsed["stage"],
        stage_version=parsed["stage_version"],
        payload={"audio_path": None} if parsed["action"] == "upload" else {},
    )
    try:
        res = drive(_SPINE, _EXECUTOR, ev)
    except Exception as e:
        logger.error(f"[spine] callback drive failed: {e}", exc_info=True)
        await query.get_bot().send_message(chat_id=query.message.chat_id,
                                           text=f"Спайн: ошибка — {e}")
        return
    await _render(query.get_bot(), query.message.chat_id, res.intents)


def register_pipeline_spine(
    application,
    *,
    claude_client,
    script_system_fn,
    cover_system_fn,
    db_path: str,
    script_model: str = "claude-opus-4-7",
    cover_model: str = "claude-opus-4-7",
    force_shorten=None,
) -> None:
    """Wire the hidden spine track into the live bot. Called once at startup.

    Dependency direction: bot.py → this adapter → content_pipeline. The Claude
    client and brand-aware prompt resolvers are injected so neither this module
    nor the core imports bot.py.
    """
    global _SPINE, _STORE, _EXECUTOR
    from telegram.ext import CommandHandler, CallbackQueryHandler  # lazy

    _STORE = PipelineStore(db_path)
    runner = BotStepRunner(
        claude_client,
        script_system_fn=script_system_fn,
        cover_system_fn=cover_system_fn,
        script_model=script_model,
        cover_model=cover_model,
        force_shorten=force_shorten,
    )
    _SPINE = PipelineSpine(_STORE)
    _EXECUTOR = EffectExecutor(_STORE, runner)

    application.add_handler(CommandHandler("spine", cmd_spine))
    application.add_handler(CommandHandler("spine_resume", cmd_spine_resume))
    application.add_handler(CallbackQueryHandler(on_spine_callback, pattern=r"^sp:"))
    logger.info(f"[spine] hidden pipeline track registered (db={db_path})")
