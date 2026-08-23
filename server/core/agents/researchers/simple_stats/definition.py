"""Simple Stats researcher — structured football statistics via MCP.

Fetches hard numbers from the ``football-analytics`` MCP server (squad shooting,
standard stats, and playing-time datasets). The live tool catalog is discovered
at gather time via ``tools/list``; ``TOOL_SPECS`` below is only the offline
fallback used when the server is unreachable, so keep it in step with the server.
"""

from __future__ import annotations

from pathlib import Path

from server.core.agents.researchers.base import ResearcherAgent, build_researcher

KEY = "simple_stats"
LABEL = "Simple Stats Researcher"
DESCRIPTION = (
    "Gathers structured, factual football statistics — player and squad output, "
    "shooting, standard per90 production, and playing time — from the football "
    "analytics warehouse."
)
SERVER = "football-analytics"
# A no-arg discovery tool the agent's decision step pre-fetches, so single-shot
# `query_data` calls can project columns and order by confirmed column names.
CATALOG_TOOL = "get_catalog"
# Structured warehouse numbers are strong + fully trusted at their tier.
STRENGTH_TIER = "strong"
SOURCE_TRUST = 1.0

PROMPT_PATH = Path(__file__).parent / "prompt.md"

# Offline fallback only — the live `football-analytics` catalog is discovered via
# tools/list at gather time and supersedes this. Mirror the server's real tools:
# the args here must match the server's input schemas exactly, because when
# discovery fails this is the ONLY schema the researcher sees — a stale shape
# makes every call error out (and silently yields no evidence).
TOOL_SPECS: list[dict] = [
    {
        "name": "get_catalog",
        "description": (
            "List the available datasets and their columns. Takes no arguments. "
            "Call first to learn which dataset and columns exist before querying. "
            "Known datasets and key columns (omit `select` if unsure to get all):\n"
            "  - squad_standard_stats: name, position, games, games_starts, "
            "minutes, minutes_90s, goals, assists, goals_assists, goals_per90, "
            "assists_per90, cards_yellow, cards_red.\n"
            "  - squad_shooting: player_name, position, minutes_90s, goals, shots, "
            "shots_on_target, shots_on_target_pct, shots_per90, goals_per_shot, "
            "goals_per_shot_on_target.\n"
            "  - squad_playing_time: player, position, games, minutes, "
            "minutes_per_game, minutes_pct, games_starts, games_subs, "
            "points_per_game, plus_minus, plus_minus_per90."
        ),
        "args_schema": {},
    },
    {
        "name": "query_data",
        "description": (
            "Query a dataset for rows/aggregates. The single argument is `request`, "
            "a QueryRequest object whose `dataset` is required. Use the datasets and "
            "columns from get_catalog (e.g. squad_standard_stats for goals/assists/"
            "per90, squad_shooting for finishing, squad_playing_time for minutes/role)."
        ),
        # The server takes ONE arg, `request`, holding the query object — not a flat
        # set of fields. Keep the nesting; a flat {dataset, ...} call is rejected.
        "args_schema": {
            "request": {
                "dataset": "string (required) — one of squad_shooting, "
                "squad_standard_stats, squad_playing_time",
                "select": ["column name"],
                "filters": {},
                "aggregations": {},
                "group_by": [],
                "order_by": "column name or null",
                "order_direction": "desc",
                "limit": 100,
            }
        },
    },
    {
        "name": "search_entities",
        "description": (
            "Resolve a player/team name to the entities present in a dataset. "
            "Requires `dataset`, `column`, and `search`; `limit` is optional."
        ),
        "args_schema": {
            "dataset": "string (required)",
            "column": "string (required) — column to search within",
            "search": "string (required) — text to match",
            "limit": 100,
        },
    },
]


def create(model_name: str | None = None) -> ResearcherAgent:
    return build_researcher(
        key=KEY,
        label=LABEL,
        server=SERVER,
        tool_specs=TOOL_SPECS,
        prompt_path=PROMPT_PATH,
        strength_tier=STRENGTH_TIER,
        source_trust=SOURCE_TRUST,
        model_name=model_name,
        catalog_tool=CATALOG_TOOL,
    )
