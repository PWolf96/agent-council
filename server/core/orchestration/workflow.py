"""Workflow protocol + the deliberation workflow.

A Workflow turns a RunConfig into a stream of events. ``DeliberationWorkflow``
wraps the evidence-grounded pipeline (`pipeline.stream_deliberation`); per-team
overrides can live in ``server/specialist_teams/<team>/workflow.py`` and are resolved by
``registry``.
"""

from __future__ import annotations

from typing import Iterator, Protocol

from server.core.config.schema import RunConfig
from server.core.orchestration.pipeline import stream_deliberation


class Workflow(Protocol):
    def stream(self, config: RunConfig, run_id: str) -> Iterator[dict]:
        ...


class DeliberationWorkflow:
    """Evidence-grounded claim-deliberation pipeline (L1→L5)."""

    def stream(self, config: RunConfig, run_id: str) -> Iterator[dict]:
        yield from stream_deliberation(
            topic=config.prompt,
            team_id=config.team_id,
            session_id=run_id,  # unique per run -> isolated evidence context
            smart_routing=config.smart_routing,
            agent_keys=config.agent_keys,
            default_model=config.default_model,
            agent_models=config.agent_models,
            max_passes=config.max_passes,
            per_pass_budget=config.per_pass_budget,
        )
