"""Plan recipes — a plan is config over ONE state machine, not a code branch.

Slice 1a ships only the ``avatar`` plan. ``selfie`` / ``broll`` are sketched so
the structure is plan-aware from day one, but their stages aren't exercised yet.
Keep this tenant-agnostic: no client names, no provider ids.
"""
from __future__ import annotations

from .models import STAGE_SCRIPT, STAGE_COVER, STAGE_VOICE, STAGE_AVATAR, STAGE_DONE

PLAN_AVATAR = "avatar"
PLAN_SELFIE = "selfie"
PLAN_BROLL = "broll"

PLANS: dict[str, dict] = {
    PLAN_AVATAR: {
        "stages": [STAGE_SCRIPT, STAGE_COVER, STAGE_VOICE, STAGE_AVATAR],
    },
    # Sketched for later slices (not driven in 1a):
    PLAN_SELFIE: {
        "stages": [STAGE_SCRIPT, STAGE_COVER, STAGE_VOICE],
    },
    PLAN_BROLL: {
        "stages": [STAGE_SCRIPT, STAGE_COVER],
    },
}


def stage_order(plan: str) -> list[str]:
    return list(PLANS[plan]["stages"])


def first_stage(plan: str) -> str:
    return stage_order(plan)[0]


def next_stage(plan: str, stage: str) -> str:
    """Stage that follows ``stage`` in ``plan`` (or ``done`` past the end)."""
    order = stage_order(plan)
    if stage not in order:
        return STAGE_DONE
    i = order.index(stage)
    return order[i + 1] if i + 1 < len(order) else STAGE_DONE
