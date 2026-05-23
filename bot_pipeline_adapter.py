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
    EV_OPEN_MATERIALS, EV_RESUME, EV_JOB_COMPLETED, EV_JOB_FAILED,
    UI_SHOW_RESULT,
    STAGE_VOICE, ST_WAITING_INPUT,
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
_STATUS_FN = None  # heygen_check_status(video_id) -> dict, injected for the poller


def _ready() -> bool:
    return _SPINE is not None and _EXECUTOR is not None


async def _render(bot, chat_id, intents) -> None:
    """Send each UIIntent as its own Telegram message (+ inline buttons).

    UI_SHOW_RESULT carries the finished video — deliver it as a video by URL.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # lazy
    for intent in intents:
        spec = intent_to_keyboard_spec(intent)
        markup = None
        if spec:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(lbl, callback_data=cb) for (lbl, cb) in row]
                 for row in spec]
            )
        if intent.kind == UI_SHOW_RESULT and (intent.data.get("url") or intent.data.get("path")):
            try:
                src = intent.data.get("url") or intent.data.get("path")
                await bot.send_video(chat_id=chat_id, video=src,
                                     caption=intent_text(intent), reply_markup=markup)
                continue
            except Exception as e:
                logger.warning(f"[spine] send_video failed, falling back to text: {e}")
        await bot.send_message(chat_id=chat_id, text=intent_text(intent), reply_markup=markup)


async def poll_jobs(context) -> None:
    """Background poller: track submitted avatar renders → completion/failure.

    Runs on the job_queue. For each run with a pending provider job it asks the
    injected status fn; on completion/failure it feeds the spine the matching
    event and delivers the result to the run's chat. Never raises (a poll error
    must not kill the job queue)."""
    if not _ready() or _STATUS_FN is None or _STORE is None:
        return
    try:
        runs = _STORE.get_runs_awaiting_job()
    except Exception as e:
        logger.error(f"[spine] poll_jobs: store query failed: {e}", exc_info=True)
        return
    for run in runs:
        try:
            st = _STATUS_FN(run.current_job_id)
        except Exception as e:
            logger.warning(f"[spine] status check failed run={run.run_id[:8]}: {e}")
            continue
        status = (st or {}).get("status")
        if status == "completed":
            ev = PipelineEvent(kind=EV_JOB_COMPLETED, run_id=run.run_id,
                               payload={"url": st.get("video_url"),
                                        "duration": st.get("duration")})
        elif status == "failed":
            ev = PipelineEvent(kind=EV_JOB_FAILED, run_id=run.run_id,
                               payload={"error": st.get("error")})
        else:
            continue  # still processing
        try:
            res = drive(_SPINE, _EXECUTOR, ev)
            if run.chat_id:
                await _render(context.bot, int(run.chat_id), res.intents)
        except Exception as e:
            logger.error(f"[spine] poll_jobs deliver failed run={run.run_id[:8]}: {e}",
                         exc_info=True)


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


async def on_spine_voice(update, context) -> None:
    """A voice message while a spine run awaits voice → upload_voice with the
    real downloaded audio. Only reached when ``_SpineAwaitingVoiceFilter`` passed,
    so the live ``process_voice`` is untouched for everything else."""
    from pathlib import Path as _Path
    user_id = str(update.effective_user.id)
    runs = [r for r in _STORE.get_active_runs(user_id)
            if r.stage == STAGE_VOICE and r.status == ST_WAITING_INPUT]
    if not runs:
        return
    run = runs[0]
    media_dir = _Path(_STORE.db_path).parent / "spine_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / f"{run.run_id}_voice.ogg"
    try:
        tg_file = await update.message.voice.get_file()
        await tg_file.download_to_drive(str(dest))
    except Exception as e:
        logger.error(f"[spine] voice download failed: {e}", exc_info=True)
        await update.message.reply_text("Спайн: не удалось скачать голосовое.")
        return
    ev = PipelineEvent(
        kind=EV_UPLOAD_VOICE, tenant="maksim", owner_user_id=user_id,
        actor_user_id=user_id, chat_id=str(update.effective_chat.id),
        run_id=run.run_id, stage=run.stage, stage_version=run.stage_version,
        payload={"audio_path": str(dest)},
    )
    try:
        res = drive(_SPINE, _EXECUTOR, ev)
    except Exception as e:
        logger.error(f"[spine] voice drive failed: {e}", exc_info=True)
        await update.message.reply_text(f"Спайн: ошибка — {e}")
        return
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
    heygen_upload_fn=None,
    heygen_generate_fn=None,
    heygen_status_fn=None,
    poll_interval: int = 20,
) -> None:
    """Wire the hidden spine track into the live bot. Called once at startup.

    Dependency direction: bot.py → this adapter → content_pipeline. The Claude
    client, brand-aware prompt resolvers and HeyGen hooks are injected so neither
    this module nor the core imports bot.py.

    If the HeyGen hooks are omitted the track still runs up to the cost-gate
    (1b behaviour); with them, ``confirm_paid`` submits a real render and the
    poller delivers the finished video (1c).
    """
    global _SPINE, _STORE, _EXECUTOR, _STATUS_FN
    from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler  # lazy
    from telegram.ext import filters as _filters

    _STORE = PipelineStore(db_path)
    runner = BotStepRunner(
        claude_client,
        script_system_fn=script_system_fn,
        cover_system_fn=cover_system_fn,
        script_model=script_model,
        cover_model=cover_model,
        force_shorten=force_shorten,
        upload_audio_fn=heygen_upload_fn,
        generate_fn=heygen_generate_fn,
    )
    _SPINE = PipelineSpine(_STORE)
    _EXECUTOR = EffectExecutor(_STORE, runner)
    _STATUS_FN = heygen_status_fn

    # Voice intake — a custom filter that matches ONLY when the sender has an
    # active spine run awaiting voice. Registered in group 0 BEFORE the live
    # process_voice (one handler per group runs), so ordinary voice messages
    # fall through to the live flow untouched. Ultra-defensive: any error → False.
    class _SpineAwaitingVoiceFilter(_filters.MessageFilter):
        def filter(self, message) -> bool:
            try:
                if _STORE is None or message.from_user is None:
                    return False
                runs = _STORE.get_active_runs(str(message.from_user.id))
                return any(r.stage == STAGE_VOICE and r.status == ST_WAITING_INPUT
                           for r in runs)
            except Exception:
                return False

    application.add_handler(
        MessageHandler((_filters.VOICE | _filters.AUDIO) & _SpineAwaitingVoiceFilter(),
                       on_spine_voice)
    )
    application.add_handler(CommandHandler("spine", cmd_spine))
    application.add_handler(CommandHandler("spine_resume", cmd_spine_resume))
    application.add_handler(CallbackQueryHandler(on_spine_callback, pattern=r"^sp:"))

    # Background poller for async provider renders (only if status hook present).
    if heygen_status_fn is not None and getattr(application, "job_queue", None):
        application.job_queue.run_repeating(poll_jobs, interval=poll_interval, first=poll_interval)
        logger.info(f"[spine] job poller registered (every {poll_interval}s)")

    logger.info(f"[spine] hidden pipeline track registered (db={db_path})")
