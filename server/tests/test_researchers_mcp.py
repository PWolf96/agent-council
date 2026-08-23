"""Researcher team (L2) + MCP client — offline, no LLM, no network.

Covers the new mechanism: the MCP client returns a clean gap when a server is
unregistered/disabled, a researcher executes decided calls *gather-only* (never
touches the Claim Ledger), and ``dispatch_research`` runs the planner-assigned
researchers (and narrows to owners on a re-gather).
"""

from __future__ import annotations

import server.mcp.client as client_mod
from server.core.agents.researchers.base import (
    MCPCall,
    ResearchActions,
    ResearcherAgent,
    ResearcherRun,
)
from server.core.evidence.models import ResearchPlan
from server.core.orchestration.researchers import dispatch_research
from server.mcp import mcp_client


# --- MCP client: unconfigured server -> clean gap ----------------------------


def test_mcp_unconfigured_returns_gap(monkeypatch):
    # Force "no server configured" regardless of servers.toml / ambient env.
    monkeypatch.setattr(client_mod, "config_for", lambda server: None)
    out = mcp_client.call_tool("nope", "get_catalog", {})
    assert isinstance(out, dict)
    assert out.get("_placeholder") is True and "not configured" in out["error"]


# --- researcher gather: decided calls, gather-only ---------------------------


def test_researcher_gather_writes_gaps_and_never_claims(ctx, monkeypatch):
    # No server reachable -> every call is a clean placeholder gap.
    monkeypatch.setattr(client_mod, "config_for", lambda server: None)

    agent = ResearcherAgent(
        model=None, system_prompt="", key="simple_stats", label="Simple Stats",
        server="stats", tool_specs=[], strength_tier="strong", source_trust=1.0,
    )
    # Skip the LLM: pretend the researcher decided one call.
    monkeypatch.setattr(
        agent, "_decide",
        lambda *a, **k: ResearchActions(calls=[MCPCall(
            tool="get_team_form", args_json='{"team": "Arsenal"}',
            covers=["Arsenal core stats"])]),
    )

    run = agent.gather("q", "brief", ctx)
    assert isinstance(run, ResearcherRun) and run.calls == 1

    rec = run.records[0]
    assert rec.source_tool == "mcp:stats.get_team_form"
    assert rec.provenance == "mcp:stats" and rec.strength_tier == "strong"
    assert rec.covers == ["Arsenal core stats"]  # the researcher's own `covers` tag
    assert rec.is_empty is True  # placeholder gap
    # Gather-only invariant: the Claim Ledger is never touched by a researcher.
    assert len(ctx.ledger.claims()) == 0


def test_bad_args_json_degrades_to_empty_args():
    call = MCPCall(tool="t", args_json="not json")
    assert call.args() == {}


# --- dispatch: run assigned researchers; narrow on re-gather -----------------


class _StubAgent:
    def __init__(self, key: str):
        self.key = key
        self.seen_brief: str | None = None

    def gather(self, question, brief, ctx, *, callbacks=None):
        self.seen_brief = brief
        rec = ctx.pool.add(
            source_tool=f"mcp:{self.key}.x", args={"q": question},
            payload={"ok": 1}, strength_tier="strong", provenance=f"mcp:{self.key}",
        )
        return ResearcherRun(key=self.key, records=[rec], calls=1)


class _StubInfo:
    def __init__(self, key: str):
        self.key = key
        self.agent = _StubAgent(key)

    def create(self, model_name=None):
        return self.agent


def _registry():
    return {k: _StubInfo(k) for k in ("simple_stats", "sentiment", "odds")}


def _plan():
    return ResearchPlan(
        question="Is City stronger than Arsenal?",
        assigned_researchers=["simple_stats", "sentiment", "odds"],
        researcher_briefs={"simple_stats": "stats brief", "odds": "odds brief"},
    )


def test_dispatch_runs_all_assigned_researchers(ctx):
    res = dispatch_research(_plan(), ctx, researchers=_registry())
    assert res.workers == 3 and res.requests_run == 3
    assert len(ctx.pool) == 3
    assert {r.provenance for r in ctx.pool.all()} == {"mcp:simple_stats", "mcp:sentiment", "mcp:odds"}
    assert len(ctx.ledger.claims()) == 0  # gather-only across the team


def test_dispatch_passes_brief_else_prompt(ctx):
    reg = _registry()
    dispatch_research(_plan(), ctx, researchers=reg)
    # A briefed researcher gets its brief; an unbriefed one falls back to the prompt.
    assert reg["odds"].agent.seen_brief == "odds brief"
    assert reg["sentiment"].agent.seen_brief == "Is City stronger than Arsenal?"
