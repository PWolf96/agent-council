"""Resolve which Workflow runs for a given team/config.

Looks for a per-team override module ``server.specialist_teams.<team_id>.workflow``
exposing ``get_workflow()`` or ``WORKFLOW``; otherwise returns the default
deliberation workflow. This is the isolation seam between agnostic core and
team-specific processing.
"""

from __future__ import annotations

import importlib

from server.core.config.schema import RunConfig
from server.core.orchestration.workflow import DeliberationWorkflow, Workflow

_default = DeliberationWorkflow()


def resolve_workflow(team_id: str, config: RunConfig) -> Workflow:
    try:
        module = importlib.import_module(f"server.specialist_teams.{team_id}.workflow")
    except ModuleNotFoundError:
        return _default

    factory = getattr(module, "get_workflow", None)
    if callable(factory):
        return factory()
    return getattr(module, "WORKFLOW", None) or _default
