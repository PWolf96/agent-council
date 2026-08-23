"""Researcher catalog — the fixed set of L2 evidence gatherers.

Unlike team specialists (discovered under ``specialist_teams/<team>/agents``), the
researcher team is general, cross-team infrastructure (like the planner and the
synthesizer), so it is a small static registry rather than a directory scan.

Each entry binds a researcher key to its persona, its MCP server label, and its
(placeholder) MCP tool catalog. The Orchestrator scores these for relevance
(:func:`score_researcher`) and the Planner picks from the admitted set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from server.core.agents.researchers.base import ResearcherAgent
from server.core.agents.researchers.odds import definition as _odds
from server.core.agents.researchers.sentiment import definition as _sentiment
from server.core.agents.researchers.simple_stats import definition as _simple_stats
from server.core.evidence.models import StrengthTier


@dataclass(frozen=True)
class ResearcherInfo:
    key: str
    label: str
    description: str
    server: str
    strength_tier: StrengthTier
    source_trust: float
    tool_specs: list[dict]
    create: Callable[..., ResearcherAgent]


def _info(mod) -> ResearcherInfo:
    return ResearcherInfo(
        key=mod.KEY,
        label=mod.LABEL,
        description=mod.DESCRIPTION,
        server=mod.SERVER,
        strength_tier=mod.STRENGTH_TIER,
        source_trust=mod.SOURCE_TRUST,
        tool_specs=mod.TOOL_SPECS,
        create=mod.create,
    )


# Insertion order is the dispatch order (kept fixed for stable evidence ids).
RESEARCHERS: dict[str, ResearcherInfo] = {
    info.key: info
    for info in (_info(_simple_stats), _info(_sentiment), _info(_odds))
}


def get_researchers() -> dict[str, ResearcherInfo]:
    return RESEARCHERS


def get_researcher(key: str) -> ResearcherInfo | None:
    return RESEARCHERS.get(key)


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "is", "are", "of", "to", "in", "for", "and", "or", "this",
    "that", "with", "be", "than", "who", "what", "which", "how", "do", "does",
    "should", "we", "i", "it", "team", "player", "season", "club",
}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOP}


def score_researcher(question: str, info: ResearcherInfo) -> float:
    """Relevance of one researcher to the question (keyword overlap, [0, ~1]).

    Mirrors ``orchestrator.score_specialist`` — deterministic and conservative,
    used only to drop the clearly irrelevant, never to finely rank.
    """
    q = _tokens(question)
    if not q:
        return 1.0
    profile = _tokens(f"{info.key} {info.label} {info.description}")
    if not profile:
        return 0.5
    return len(q & profile) / max(1, len(profile))
