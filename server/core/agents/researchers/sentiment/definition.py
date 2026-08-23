"""Sentiment researcher — fan-voice / qualitative signal via MCP.

Fetches qualitative sentiment (fan opinions, channel chatter, mood) from the
``sentiment`` MCP server. Placeholder tool catalog: edit ``TOOL_SPECS`` to match
your MCP server.
"""

from __future__ import annotations

from pathlib import Path

from server.core.agents.researchers.base import ResearcherAgent, build_researcher

KEY = "sentiment"
LABEL = "Sentiment Researcher"
DESCRIPTION = (
    "Gathers qualitative fan-voice signal — supporter opinions, mood, morale, "
    "manager and key-player sentiment — from fan channels and discussion."
)
SERVER = "sentiment"
# Opinion, not measurement: moderate tier and a discounted source trust.
STRENGTH_TIER = "moderate"
SOURCE_TRUST = 0.55

PROMPT_PATH = Path(__file__).parent / "prompt.md"

# TODO(you): align these with the tools your `sentiment` MCP server exposes.
TOOL_SPECS: list[dict] = [
    {
        "name": "search_fan_opinions",
        "description": "Semantic search over fan-channel transcripts for a query.",
        "args_schema": {"query": "string", "club": "string", "top_k": "int"},
    },
    {
        "name": "get_recent_fan_clips",
        "description": "Most recent fan-channel clips/discussion for a club.",
        "args_schema": {"club": "string", "top_k": "int"},
    },
    {
        "name": "list_fan_channels",
        "description": "Available fan channels (use to scope a search).",
        "args_schema": {"club": "string"},
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
