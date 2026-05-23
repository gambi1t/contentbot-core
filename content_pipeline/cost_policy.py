"""Cost policy — which stages spend real money and must be gated.

The invariant: auto-advance NEVER triggers a paid step. A paid stage stops the
run at ``waiting_confirm`` and only an explicit ``confirm_paid`` event may
release the provider job.

Slice 1a: the avatar (HeyGen) stage is the single gated step. The TTS voice
path (ElevenLabs, also paid) is deferred to 1b — 1a uses the free "own voice"
upload, so there is exactly one gate to prove the invariant against.
"""
from __future__ import annotations

from .models import STAGE_AVATAR

# Stages that cost money on each run → require an explicit cost-gate confirm.
PAID_STAGES: set[str] = {STAGE_AVATAR}


def is_paid_stage(stage: str) -> bool:
    return stage in PAID_STAGES
