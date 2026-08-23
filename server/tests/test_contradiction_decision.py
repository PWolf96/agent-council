"""Contradiction resolution (L5a) + Decision aggregation (L5), deterministic."""

from server.core.evidence.models import ResearchPlan, SufficiencyReport
from server.core.orchestration.contradiction import find_conflicts, resolve_contradictions
from server.core.orchestration.decision import aggregate, decide


def _setup_conflict(ctx):
    # Shared evidence E02 read oppositely by two different owners.
    ctx.store.add(source_tool="quant:strength", args={}, payload={"summary": "x"},
                  strength_tier="authoritative", provenance="quant:strength")  # E01
    ctx.store.add(source_tool="get_squad_stats", args={}, payload={},
                  strength_tier="strong", provenance="postgres")  # E02
    a = ctx.ledger.add_claim(owner="Tactical", dimension="tactical",
                             assertion="City are clearly the stronger, dominant side.",
                             evidence_ids=["E01", "E02"], confidence=0.78)
    b = ctx.ledger.add_claim(owner="Context", dimension="context",
                             assertion="City carry fatigue risk and weakness, a concern.",
                             evidence_ids=["E02"], confidence=0.6)
    for c in (a, b):
        ctx.ledger.set_status(c.claim_id, "resolved")
    return a, b


def test_find_conflicts_detects_opposing_polarity(ctx):
    a, b = _setup_conflict(ctx)
    conflicts = find_conflicts(ctx)
    pairs = {frozenset((x.claim_id, y.claim_id)) for x, y in conflicts}
    assert frozenset((a.claim_id, b.claim_id)) in pairs


def test_resolution_picks_stronger_evidence(ctx):
    a, b = _setup_conflict(ctx)
    outcomes = resolve_contradictions(ctx)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.resolution == "dominant"
    assert o.winner == a.claim_id  # backed by authoritative quant evidence


def test_no_conflict_when_same_owner(ctx):
    ctx.store.add(source_tool="t", args={}, payload={}, strength_tier="strong",
                  provenance="postgres")  # E01
    ctx.ledger.add_claim(owner="Same", dimension="d", assertion="strong dominant clear",
                         evidence_ids=["E01"], confidence=0.7)
    ctx.ledger.add_claim(owner="Same", dimension="d", assertion="weak risk concern",
                         evidence_ids=["E01"], confidence=0.7)
    assert find_conflicts(ctx) == []


def test_decision_confidence_weighted_and_mandatory_dissent(ctx):
    a, b = _setup_conflict(ctx)
    plan = ResearchPlan(question="q", question_type="comparison")
    cons = resolve_contradictions(ctx)
    suff = SufficiencyReport(sufficient=True, missing_evidence=["fan sentiment"], blocking=False)
    agg = aggregate(ctx, plan, cons, suff)
    assert 0.05 <= agg.decision_confidence <= 0.95
    # Dissent must include the out-weighed claim AND the stated limitation.
    summaries = " ".join(d.summary for d in agg.dissent)
    assert "fan sentiment" in summaries
    assert any(d.claim_id == b.claim_id for d in agg.dissent)


def test_decide_offline_produces_answer(ctx):
    _setup_conflict(ctx)
    plan = ResearchPlan(question="Is City stronger?", question_type="comparison")
    cons = resolve_contradictions(ctx)
    decision, _agg = decide("Is City stronger?", ctx, plan, cons, None, use_llm=False)
    assert decision.answer  # fallback narrative
    assert decision.supporting_claims
    assert decision.citations
