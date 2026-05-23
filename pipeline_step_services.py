"""Headless step services for the pipeline spine (bot-layer side).

These are the *real* ``StepRunner`` implementations the spine's EffectExecutor
calls — the production counterpart of ``content_pipeline.steps.MockStepRunner``.

Design rules (so the spine core stays pure and this stays portable):
  * this module does NOT ``import bot`` — importing bot.py would boot the whole
    Telegram app. Instead bot.py constructs ``BotStepRunner`` and INJECTS its
    own objects (the Claude client + brand-aware prompt resolvers);
  * services are HEADLESS — they take data, return artifacts, never send
    Telegram messages and never touch ``pending``/global bot state;
  * brand-awareness is honoured at call time via injected resolver callables, so
    the right system prompt is used without baking any client into this module.

Script + cover run via Claude. ``start_paid_job`` submits a real HeyGen render
when the provider hooks are injected (own voice → asset upload → generate); the
background poller tracks completion. Without the hooks injected it raises (a
safety no-go used by the unit tests / a hooks-less deployment).
"""
from __future__ import annotations

from typing import Callable, Optional

from content_pipeline.steps import PreSubmitError


class BotStepRunner:
    """Concrete StepRunner backed by the bot's Claude client + prompts.

    Parameters are injected by bot.py at startup:
      * ``claude_client``      — the Anthropic client (``.messages.create``);
      * ``script_system_fn``   — () -> str, brand-resolved script system prompt;
      * ``cover_system_fn``    — () -> str, brand-resolved cover system prompt;
      * ``script_model`` / ``cover_model`` — model ids;
      * ``force_shorten``      — optional (text) -> text post-processor.
    """

    def __init__(
        self,
        claude_client,
        *,
        script_system_fn: Callable[[], str],
        cover_system_fn: Callable[[], str],
        script_model: str = "claude-opus-4-7",
        cover_model: str = "claude-opus-4-7",
        force_shorten: Optional[Callable[[str], str]] = None,
        upload_audio_fn: Optional[Callable[[str], str]] = None,
        generate_fn: Optional[Callable[..., str]] = None,
    ) -> None:
        self.claude = claude_client
        self.script_system_fn = script_system_fn
        self.cover_system_fn = cover_system_fn
        self.script_model = script_model
        self.cover_model = cover_model
        self.force_shorten = force_shorten
        # Injected provider hooks (1c). When absent → start_paid_job stays a
        # no-go (1b behaviour). bot.py wires real HeyGen upload + generate here.
        self.upload_audio_fn = upload_audio_fn
        self.generate_fn = generate_fn

    # ── StepRunner protocol ───────────────────────────────────────────────────
    def generate_script(self, run_id: str, idea_text: str, config: dict) -> dict:
        resp = self.claude.messages.create(
            model=self.script_model,
            max_tokens=1024,
            system=self.script_system_fn(),
            messages=[{"role": "user", "content": idea_text}],
        )
        text = resp.content[0].text.strip()
        # Mirror bot.py: drop a leading "СЦЕНАРИЙ:" label if the model adds one.
        if text.upper().startswith("СЦЕНАРИЙ"):
            text = text.split("\n", 1)[-1].strip()
        if self.force_shorten is not None:
            text = self.force_shorten(text)
        return {"text_content": text, "meta": {}}

    def generate_cover_options(self, run_id: str, script_text: str, config: dict) -> dict:
        resp = self.claude.messages.create(
            model=self.cover_model,
            max_tokens=300,
            system=self.cover_system_fn(),
            messages=[{
                "role": "user",
                "content": (
                    f"Сценарий:\n{script_text}\n\nПридумай 5 вирусных текстов "
                    "для обложки. Каждый на новой строке, только текст, без нумерации."
                ),
            }],
        )
        raw = resp.content[0].text.strip()
        options = [
            ln.strip().strip('"').strip("«»").strip("-").strip()
            for ln in raw.split("\n") if ln.strip()
        ]
        options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]
        return {"meta": {"options": options}}

    def start_paid_job(self, run_id: str, kind: str, config: dict) -> str:
        """Submit the paid avatar render and return the provider job id.

        Headless: uploads the voice audio + submits generation, returns
        immediately (the render takes minutes — the poller tracks completion).
        Requires the provider hooks to be injected (1c); without them this is a
        no-go (1b safety).
        """
        # Pre-spend failures → PreSubmitError so the spine rolls back to the
        # cost-gate (retryable), instead of treating it as a maybe-charged job.
        if self.upload_audio_fn is None or self.generate_fn is None:
            raise PreSubmitError("provider hooks not injected")
        audio_path = config.get("audio_path")
        if not audio_path:
            raise PreSubmitError("no voice audio for the run")
        try:
            audio_url = self.upload_audio_fn(audio_path)
        except Exception as e:  # upload failed → no render job created yet
            raise PreSubmitError(f"audio upload failed: {e}") from e
        # A failure in generate_fn is AMBIGUOUS (HeyGen may have created the job)
        # → let it propagate as a plain Exception; the spine treats it as
        # unknown-after-submit and does NOT auto-retry (no double-charge).
        return self.generate_fn(
            audio_url,
            config.get("look_id"),
            config.get("avatar_version", "v3"),
        )
