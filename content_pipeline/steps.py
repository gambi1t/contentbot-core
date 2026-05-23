"""Step runners — headless services that DO the work and return artifacts.

Hard rule: a step runner returns data, never sends Telegram messages and never
touches ``pending``/global bot state. That keeps it reusable by both the
Telegram adapter and a future Mini App adapter, and portable into the
constructor.

Slice 1a uses :class:`MockStepRunner` exclusively — no real provider calls.
1b will introduce real ``ScriptService`` / ``CoverService`` by extracting the
pure parts out of ``bot.py``.
"""
from __future__ import annotations

from typing import Protocol


class StepRunner(Protocol):
    """Interface the spine depends on. Implementations are headless."""

    def generate_script(self, run_id: str, idea_text: str, config: dict) -> dict:
        """Return {'text_content': str, 'meta': dict}."""
        ...

    def generate_cover_options(self, run_id: str, script_text: str, config: dict) -> dict:
        """Return {'meta': {'options': [...]}}."""
        ...

    def start_paid_job(self, run_id: str, kind: str, config: dict) -> str:
        """Kick off the external paid provider job; return its job id."""
        ...


class MockStepRunner:
    """Deterministic fake for Slice 1a tests — costs nothing, calls nobody."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def generate_script(self, run_id: str, idea_text: str, config: dict) -> dict:
        self.calls.append(("generate_script", run_id))
        return {"text_content": f"[mock script for] {idea_text}", "meta": {}}

    def generate_cover_options(self, run_id: str, script_text: str, config: dict) -> dict:
        self.calls.append(("generate_cover_options", run_id))
        return {"meta": {"options": ["mock cover A", "mock cover B", "mock cover C"]}}

    def start_paid_job(self, run_id: str, kind: str, config: dict) -> str:
        self.calls.append(("start_paid_job", run_id, kind))
        return f"mock-job-{run_id[:8]}"
