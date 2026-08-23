"""Team catalog for the wizard (team -> agents with descriptions)."""

from __future__ import annotations

from fastapi import APIRouter

from server.core.agents.teams import get_all_teams

router = APIRouter(prefix="/api/teams", tags=["teams"])


@router.get("")
def list_teams():
    teams = get_all_teams()
    return [
        {
            "id": team.id,
            "name": team.name,
            "agents": [
                {"key": a.key, "label": a.label, "description": a.description}
                for a in team.agents
            ],
        }
        for team in teams.values()
    ]
