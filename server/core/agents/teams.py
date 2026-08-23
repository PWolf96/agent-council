"""Team + agent discovery.

Teams live under ``server/specialist_teams/<team_id>/`` with a ``team.json`` and
an ``agents/<agent>/definition.py`` per agent. This is workflow-agnostic runtime:
it just discovers what teams/agents exist; the per-team processing itself lives
in those specialist-team folders.
"""

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import server as _server_pkg

# Anchor on the top-level package dir so this is robust to file depth.
_SERVER_DIR = Path(_server_pkg.__file__).resolve().parent
_TEAMS_DIR = _SERVER_DIR / "specialist_teams"


@dataclass(frozen=True)
class AgentInfo:
    key: str
    label: str
    factory: Callable
    prompt_path: Path
    description: str = ""


@dataclass(frozen=True)
class TeamInfo:
    id: str
    name: str
    agents: tuple[AgentInfo, ...]


def _label_from_key(key: str) -> str:
    return key.replace("_", " ").title()


def _discover_agents_in(
    agents_dir: Path,
    team_id: str,
    descriptions: dict[str, str],
) -> list[AgentInfo]:
    agents: list[AgentInfo] = []
    if not agents_dir.is_dir():
        return agents
    for child in sorted(agents_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        definition_path = child / "definition.py"
        if not definition_path.exists():
            continue

        key = child.name
        module_path = f"server.specialist_teams.{team_id}.agents.{key}.definition"
        mod = importlib.import_module(module_path)

        factory_name = f"create_{key}_agent"
        factory = getattr(mod, factory_name, None)
        if factory is None:
            continue

        prompt_path = child / "prompt.md"
        agents.append(AgentInfo(
            key=key,
            label=_label_from_key(key),
            factory=factory,
            prompt_path=prompt_path,
            description=descriptions.get(key, ""),
        ))
    return agents


def discover_teams() -> dict[str, TeamInfo]:
    teams: dict[str, TeamInfo] = {}
    if not _TEAMS_DIR.is_dir():
        return teams
    for child in sorted(_TEAMS_DIR.iterdir()):
        if not child.is_dir():
            continue
        config_path = child / "team.json"
        if not config_path.exists():
            continue

        team_id = child.name
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        descriptions = config.get("agent_descriptions", {}) or {}
        agents = _discover_agents_in(child / "agents", team_id, descriptions)
        if not agents:
            continue

        teams[team_id] = TeamInfo(
            id=team_id,
            name=config.get("name", team_id),
            agents=tuple(agents),
        )
    return teams


_teams: dict[str, TeamInfo] | None = None


def _ensure_loaded() -> dict[str, TeamInfo]:
    global _teams
    if _teams is None:
        _teams = discover_teams()
    return _teams


def get_all_teams() -> dict[str, TeamInfo]:
    return _ensure_loaded()


def get_team(team_id: str) -> TeamInfo:
    teams = _ensure_loaded()
    if team_id not in teams:
        raise ValueError(f"Unknown team: {team_id!r}. Available: {list(teams)}")
    return teams[team_id]


def find_agent_by_key(agent_key: str) -> AgentInfo | None:
    for team in _ensure_loaded().values():
        for agent in team.agents:
            if agent.key == agent_key:
                return agent
    return None
