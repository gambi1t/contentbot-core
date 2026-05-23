"""content_pipeline — tenant-agnostic pipeline orchestrator («спайн»).

Parallel-track state machine that drives a content card from idea to a
ready-to-render video. Deliberately decoupled from the live Telegram bot:

  * the CORE (this package) never imports ``bot.py`` and contains NO
    tenant-specific constants (no client name, no provider avatar ids, no
    Notion status strings, no Telegram calls);
  * the core emits two clean streams — ``UIIntent`` (what to show) and
    ``EffectCommand`` (what to actually do, incl. paid provider jobs);
  * runtime truth lives in SQLite (:mod:`content_pipeline.store`); Notion is a
    human-readable mirror only.

Slice 1a scope: the linear ``avatar`` plan up to the avatar cost-gate, on a
MOCK step runner — no real provider calls. See ``docs/pipeline_spine/``.
"""

from .models import (  # noqa: F401
    PipelineEvent,
    UIIntent,
    UIAction,
    InputField,
    EffectCommand,
    Decision,
    Run,
    Artifact,
)
from .store import PipelineStore  # noqa: F401
from .core import PipelineSpine, EffectExecutor, drive, DriveResult  # noqa: F401
from .steps import StepRunner, MockStepRunner  # noqa: F401

__all__ = [
    "PipelineEvent",
    "UIIntent",
    "UIAction",
    "InputField",
    "EffectCommand",
    "Decision",
    "Run",
    "Artifact",
    "PipelineStore",
    "PipelineSpine",
    "EffectExecutor",
    "drive",
    "DriveResult",
    "StepRunner",
    "MockStepRunner",
]
