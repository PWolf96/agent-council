"""Deliverable shape (L1) + entity-aware Decision (L5).

Covers the split of the two axes: ``question_type`` stays epistemic, while the
open-ended ``DeliverableSpec`` drives the answer's shape. The structural payoff is
that a "top N" question is scored per-subject and rendered as a ranking instead of
collapsing onto a single strongest claim.
"""

from dataclasses import dataclass

from server.core.agents.general.planner import build_research_plan
from server.core.agents.general.planner.definition import _extract_cardinality
from server.core.evidence.models import DeliverableSpec, ResearchPlan
from server.core.orchestration.decision import aggregate, decide


@dataclass
class _AgentStub:
    key: str
    label: str
    description: str = ""


_AGENTS = [_AgentStub("current_form_analyst", "Current Form Analyst"),
           _AgentStub("tactical_performance_analyst", "Tactical Performance Analyst")]


# --- planner: deliverable derivation ----------------------------------------


def test_extract_cardinality_variants():
    assert _extract_cardinality("Give me the top 5 most effective players") == 5
    assert _extract_cardinality("rank the 3 best strikers") == 3
    assert _extract_cardinality("list 10 wingers") == 10
    assert _extract_cardinality("who is the strongest team?") == 0


def test_heuristic_plan_marks_list_deliverable():
    plan = build_research_plan(
        "Give me a list of the top 5 most effective players", _AGENTS, use_llm=False
    )
    assert plan.deliverable.cardinality == 5
    assert plan.deliverable.is_list_like()
    # Epistemic axis is untouched by the list shape.
    assert plan.question_type == "scouting"


def test_default_deliverable_is_single_verdict():
    plan = build_research_plan("Is Man City stronger than Arsenal?", _AGENTS, use_llm=False)
    assert plan.deliverable.cardinality == 0
    assert not plan.deliverable.is_list_like()


# --- decision: entity-aware ranking -----------------------------------------


def _two_player_ctx(ctx):
    ctx.store.add(source_tool="query_data", args={"p": "Saka"}, payload={"g": 19},
                  strength_tier="strong", provenance="postgres")  # E01
    ctx.store.add(source_tool="query_data", args={"p": "Saliba"}, payload={"g": 3},
                  strength_tier="strong", provenance="postgres")  # E02
    c1 = ctx.ledger.add_claim(
        owner="Current Form Analyst", dimension="attack",
        assertion="Bukayo Saka is a highly effective, decisive attacker.",
        evidence_ids=["E01"], confidence=0.78)
    c2 = ctx.ledger.add_claim(
        owner="Tactical Performance Analyst", dimension="defense",
        assertion="William Saliba is solid but less decisive going forward.",
        evidence_ids=["E02"], confidence=0.55)
    for c in (c1, c2):
        ctx.ledger.set_status(c.claim_id, "resolved")
    return c1, c2


def _list_plan():
    return ResearchPlan(
        question="top 2 players",
        question_type="scouting",
        deliverable=DeliverableSpec(
            format="a ranked list of the top 2 players with a grade each",
            cardinality=2,
            subjects=["Bukayo Saka", "William Saliba"],
        ),
    )


def test_aggregate_ranks_named_subjects(ctx):
    _two_player_ctx(ctx)
    agg = aggregate(ctx, _list_plan(), [], None)
    names = [e.entity for e in agg.entity_scores]
    assert names == ["Bukayo Saka", "William Saliba"]  # higher-confidence first
    assert agg.entity_scores[0].score >= agg.entity_scores[1].score


def test_single_verdict_has_no_entity_scores(ctx):
    _two_player_ctx(ctx)
    plan = ResearchPlan(question="who is best?", deliverable=DeliverableSpec())
    agg = aggregate(ctx, plan, [], None)
    assert agg.entity_scores == []


def test_decide_offline_meets_list_shape(ctx):
    _two_player_ctx(ctx)
    decision, agg = decide("top 2 players", ctx, _list_plan(), [], None, use_llm=False)
    # Even the no-LLM fallback returns the requested shape: both subjects, ranked.
    assert "Saka" in decision.answer
    assert "Saliba" in decision.answer
    assert agg.entity_scores


def test_unmatched_subjects_are_dropped_not_zeroed(ctx):
    _two_player_ctx(ctx)
    plan = ResearchPlan(
        question="top 3 players",
        deliverable=DeliverableSpec(
            format="ranked list", cardinality=3,
            subjects=["Bukayo Saka", "William Saliba", "Gabriel Jesus"],
        ),
    )
    agg = aggregate(ctx, plan, [], None)
    # Jesus has no claim, so he is omitted rather than ranked last at score 0.
    assert [e.entity for e in agg.entity_scores] == ["Bukayo Saka", "William Saliba"]
