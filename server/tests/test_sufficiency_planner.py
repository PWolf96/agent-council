"""Sufficiency gate (L2a) coverage check + Research Planner (L1) heuristic path."""

from dataclasses import dataclass

from server.core.agents.general.planner import build_research_plan, heuristic_sketch
from server.core.evidence.models import EvidenceRequest, ResearchPlan
from server.core.orchestration.sufficiency import critical_gap_labels, review_sufficiency


@dataclass
class _AgentStub:
    key: str
    label: str
    description: str = ""


_AGENTS = [
    _AgentStub("current_form_analyst", "Current Form Analyst"),
    _AgentStub("tactical_performance_analyst", "Tactical Performance Analyst"),
    _AgentStub("context_sentiment_analyst", "Context Sentiment Analyst"),
]


# --- sufficiency -------------------------------------------------------------


def _plan_with(reqs):
    return ResearchPlan(question="q", required_evidence=reqs)


def test_sufficient_when_critical_covered(ctx):
    plan = _plan_with([
        EvidenceRequest(label="A form", tool="get_team_form", args={}, critical=True),
        EvidenceRequest(label="A fans", tool="search_fan_opinions", args={}, critical=False),
    ])
    ctx.store.add(source_tool="get_team_form", args={}, payload={"x": 1},
                  strength_tier="strong", provenance="postgres", covers=["A form"])
    # fan record came back empty (qdrant down) -> non-critical gap
    ctx.store.add(source_tool="search_fan_opinions", args={}, payload={"error": "down"},
                  strength_tier="moderate", provenance="qdrant", covers=["A fans"], is_empty=True)

    report = review_sufficiency(plan, ctx)
    assert report.sufficient is True
    assert report.blocking is False
    assert "A fans" in report.missing_evidence
    assert critical_gap_labels(plan, report) == set()


def test_blocking_on_critical_gap(ctx):
    plan = _plan_with([
        EvidenceRequest(label="A form", tool="get_team_form", args={}, critical=True),
    ])
    ctx.store.add(source_tool="get_team_form", args={}, payload={"error": "no data"},
                  strength_tier="strong", provenance="postgres", covers=["A form"], is_empty=True)
    report = review_sufficiency(plan, ctx)
    assert report.blocking is True
    assert report.sufficient is False
    assert critical_gap_labels(plan, report) == {"A form"}


# --- planner heuristic -------------------------------------------------------


def test_heuristic_classifies_comparison():
    sketch = heuristic_sketch("Is Manchester City stronger than Arsenal?", _AGENTS)
    assert sketch.question_type == "comparison"
    assert "Manchester City" in sketch.teams and "Arsenal" in sketch.teams
    assert "strength" in sketch.quant_models


def test_heuristic_classifies_probability():
    sketch = heuristic_sketch("What is the probability Bayern beats Real Madrid?", _AGENTS)
    assert sketch.question_type == "probability"
    assert "win_probability" in sketch.quant_models


def test_plan_assigns_researchers_with_briefs():
    # L2 is researcher-owned: the plan just selects researchers (here the heuristic
    # falls back to the whole admitted pool) and briefs each with the prompt. It
    # declares NO evidence needs — each researcher decides what to fetch itself.
    pool = ["simple_stats", "sentiment", "odds"]
    q = "Is Man City stronger than Arsenal this season?"
    plan = build_research_plan(q, _AGENTS, researchers=pool, use_llm=False)
    assert set(plan.assigned_researchers) == set(pool)
    assert set(plan.researcher_briefs) == set(plan.assigned_researchers)
    assert all(plan.researcher_briefs[k] for k in plan.assigned_researchers)
    assert plan.required_evidence == []  # planner does not enumerate fetches


def test_plan_falls_back_to_all_specialists():
    plan = build_research_plan("Some vague question", _AGENTS, use_llm=False)
    assert set(plan.assigned_specialists) == {a.key for a in _AGENTS}
