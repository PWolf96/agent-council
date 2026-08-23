"""Odds researcher — betting-market signal via MCP.

Fetches market odds (match markets, outrights, movement) from the ``odds`` MCP
server. Markets aggregate a lot of information, so this evidence is treated as
authoritative at its tier. Placeholder tool catalog: edit ``TOOL_SPECS`` to
match your MCP server.
"""

from __future__ import annotations

from pathlib import Path

from server.core.agents.researchers.base import ResearcherAgent, build_researcher

KEY = "odds"
LABEL = "Odds Researcher"
DESCRIPTION = (
    "Gathers betting-market signal — match odds (1X2, over/under), outright "
    "/ futures prices, and line movement — as a market-implied probability read."
)
SERVER = "odds"
# Markets price in broad information; treat as authoritative at their tier.
STRENGTH_TIER = "authoritative"
SOURCE_TRUST = 1.0

PROMPT_PATH = Path(__file__).parent / "prompt.md"

# TODO(you): align these with the tools your `odds` MCP server exposes.
TOOL_SPECS: list[dict] = [
    {
        "name": "get_match_odds",
        "description": "Current match-market odds (1X2, over/under) for a fixture.",
        "args_schema": {
            "team_a": "string",
            "team_b": "string",
            "market": "string",
        },
    },
    {
        "name": "get_outright_odds",
        "description": "Outright / futures prices for a competition (e.g. winner).",
        "args_schema": {"competition": "string", "selection": "string"},
    },
    {
        "name": "get_odds_movement",
        "description": "How a market's price has moved over a recent window.",
        "args_schema": {
            "team_a": "string",
            "team_b": "string",
            "market": "string",
            "window_days": "int",
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
    )
