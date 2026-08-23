"""v3-specific coverage: roster (L0), researcher pool (L2), sufficiency tiers/
negatives (L2a), source_trust confidence (L4), evaluator (L6), and the offline
L4->L6 wiring (sweep -> contradiction -> decision -> evaluator)."""

from dataclasses import dataclass

from server.core.evidence.confidence import refutation, update_confidence
from server.core.evidence.models import (
    AuthorResponse,
    ChallengeDraft,
    Decision,
    Dissent,
    EvidenceRequest,
    ResearchPlan,
    SufficiencyReport,
)
from server.core.orchestration.contradiction import resolve_contradictions
from server.core.orchestration.crux import find_cruxes
from server.core.orchestration.decision import decide
from server.core.orchestration.deliberation import AdversarialReview
from server.core.orchestration.evaluator import evaluate, retry_target
from server.core.orchestration.orchestrator import Budget, RunState, select_roster
from server.core.orchestration.sufficiency import review_sufficiency
from server.mcp.payloads import is_negative_result as _is_negative_result


@dataclass
class _AgentStub:
    key: str
    label: str
    description: str = ""


# --- L0 roster selection -----------------------------------------------------


def test_roster_drops_clearly_irrelevant_specialist():
    agents = [
        _AgentStub("current_form_analyst", "Current Form Analyst", "football team recent form"),
        _AgentStub("tactical_performance_analyst", "Tactical Performance Analyst", "football tactics"),
        _AgentStub("commodities_analyst", "Commodities Analyst", "oil and metals futures pricing"),
    ]
    roster = select_roster("Is Arsenal stronger than Chelsea in football this season?",
                           agents, question_type="comparison")
    assert "current_form_analyst" in roster.admitted_specialists
    assert "commodities_analyst" not in roster.admitted_specialists
    assert any(d["agent"] == "commodities_analyst" for d in roster.dropped)


def test_roster_conservative_keeps_all_when_sparse():
    # Two agents, none sharing a salient term -> never strand a perspective.
    agents = [_AgentStub("a", "A", "alpha"), _AgentStub("b", "B", "beta")]
    roster = select_roster("totally unrelated query", agents, question_type="scouting")
    assert set(roster.admitted_specialists) == {"a", "b"}


def test_roster_admits_full_researcher_pool():
    # Researcher *selection* is the Planner's job, so the roster exposes the whole
    # (small, fixed) researcher team for the planner to pick + brief from.
    agents = [_AgentStub("x", "X", "football")]
    roster = select_roster("What is the probability Bayern beats Real Madrid?",
                           agents, question_type="probability")
    assert set(roster.admitted_researchers) == {"simple_stats", "sentiment", "odds"}


# --- L2 researcher pool: see test_researchers_mcp.py for dispatch + MCP -------


def test_negative_result_vs_error():
    assert _is_negative_result({"results": []}) is True           # searched, absent
    assert _is_negative_result({"matched_players": []}) is True
    assert _is_negative_result({"error": "qdrant down"}) is False  # failure, not absence
    assert _is_negative_result({"results": [1]}) is False


# --- L2a sufficiency: tiers + negative-result coverage -----------------------


def test_sufficiency_min_tier_blocks_soft_signal(ctx):
    plan = ResearchPlan(question="q", required_evidence=[
        EvidenceRequest(label="hard fact", tool="get_team_form", critical=True, min_tier="strong"),
    ])
    # Only a weak (soft-signal) record covers the slot -> does NOT satisfy min_tier.
    ctx.pool.add(source_tool="get_team_form", args={}, payload={"x": 1},
                 strength_tier="weak", provenance="qdrant", covers=["hard fact"])
    report = review_sufficiency(plan, ctx)
    assert report.blocking is True
    assert "hard fact" in report.missing_evidence


def test_sufficiency_negative_result_covers_slot(ctx):
    plan = ResearchPlan(question="q", required_evidence=[
        EvidenceRequest(label="injury record", tool="get_injuries", critical=True),
    ])
    # "searched, provably absent" -> slot resolved by absence (a stated limitation).
    ctx.pool.add(source_tool="get_injuries", args={}, payload={"results": []},
                 strength_tier="weak", provenance="postgres", covers=["injury record"],
                 is_empty=True, is_negative_result=True)
    report = review_sufficiency(plan, ctx)
    assert report.blocking is False
    assert "injury record" in report.covered


# --- L4 confidence: source_trust dampening -----------------------------------


def test_source_trust_dampens_confidence():
    base = 0.6
    trusted = update_confidence(base, [refutation("strong", source_trust=1.0)])
    noisy = update_confidence(base, [refutation("strong", source_trust=0.3)])
    # A low-trust source at the same tier moves confidence less.
    assert trusted < noisy < base


# --- L6 evaluator ------------------------------------------------------------


def _decided(ctx):
    ctx.pool.add(source_tool="quant:strength", args={}, payload={"summary": "x"},
                 strength_tier="authoritative", provenance="quant:strength")  # E01
    ctx.pool.add(source_tool="get_squad_stats", args={}, payload={},
                 strength_tier="strong", provenance="postgres")  # E02
    a = ctx.ledger.add_claim(owner="Tactical", dimension="tactical",
                             assertion="City are clearly the stronger, dominant side.",
                             evidence_ids=["E01", "E02"], confidence=0.78)
    b = ctx.ledger.add_claim(owner="Context", dimension="context",
                             assertion="City carry fatigue risk and weakness, a concern.",
                             evidence_ids=["E02"], confidence=0.6)
    for c in (a, b):
        ctx.ledger.set_status(c.claim_id, "resolved")
    plan = ResearchPlan(question="Is City stronger?", question_type="comparison")
    cons = resolve_contradictions(ctx)
    decision, agg = decide("Is City stronger?", ctx, plan, cons, None,
                           open_cruxes=find_cruxes(ctx), use_llm=False)
    return decision, agg, plan


def test_evaluator_passes_clean_decision(ctx):
    decision, agg, plan = _decided(ctx)
    card = evaluate(decision, ctx, question_type=plan.question_type,
                    aggregate_confidence=agg.decision_confidence,
                    expected_dissent_ids={d.claim_id for d in agg.dissent}, sufficiency=None)
    assert card.passed
    assert card.grounding_ok and card.calibration_ok and card.completeness_ok
    assert retry_target(card) is None


def test_evaluator_flags_miscalibration(ctx):
    decision, agg, plan = _decided(ctx)
    # Pretend the aggregation disagrees with the stated confidence.
    card = evaluate(decision, ctx, question_type=plan.question_type,
                    aggregate_confidence=agg.decision_confidence + 0.2,
                    expected_dissent_ids={d.claim_id for d in agg.dissent}, sufficiency=None)
    assert not card.passed and card.failure == "calibration"
    assert retry_target(card) == "aggregator"


def test_evaluator_flags_unsurfaced_completeness_gap(ctx):
    # A blocking gap that the decision never surfaces -> completeness fails.
    decision = Decision(answer="x", confidence=0.5, confidence_kind="judgmental",
                        unresolved_dissent=[], citations=[])
    suff = SufficiencyReport(sufficient=False, missing_evidence=["salary expectations"],
                             blocking=True)
    card = evaluate(decision, ctx, question_type="valuation", aggregate_confidence=0.5,
                    expected_dissent_ids=set(), sufficiency=suff)
    assert not card.passed and card.failure == "completeness"
    assert card.missing == ["salary expectations"]
    assert retry_target(card) == "planner"


# --- L4 -> L6 offline wiring -------------------------------------------------


class _Bull:
    label, dimension, key = "Tactical", "tactical", "tac"

    def review(self, q, others, digest, valid_ids, callbacks=None):
        return []

    def respond(self, claim, challenge, digest, valid_ids, callbacks=None):
        return AuthorResponse(action="cite", evidence_ids=list(claim.evidence_ids))


class _Bear(_Bull):
    label, dimension, key = "Context", "context", "ctx"

    def __init__(self, target_getter, ev):
        self._t, self._ev, self.calls = target_getter, ev, 0

    def review(self, q, others, digest, valid_ids, callbacks=None):
        self.calls += 1
        if self.calls == 1:
            return [ChallengeDraft(target_claim=self._t(), kind="contradicting_evidence",
                                   evidence_ids=[self._ev], severity="major",
                                   rationale="quant cuts against this")]
        return []


def test_sweep_to_decision_to_evaluator_offline(ctx):
    e1 = ctx.pool.add(source_tool="get_squad_stats", args={}, payload={"x": 1},
                      strength_tier="strong", provenance="postgres", covers=["squad"])
    e2 = ctx.pool.add(source_tool="quant:strength", args={}, payload={"summary": "tight"},
                      strength_tier="authoritative", provenance="quant:strength", covers=["q"])
    a = ctx.ledger.add_claim(owner="Tactical", dimension="tactical",
                             assertion="Alpha are clearly the stronger, dominant side.",
                             evidence_ids=[e1.id], confidence=0.7)
    ctx.ledger.add_claim(owner="Context", dimension="context",
                         assertion="Alpha carry real fatigue risk and weakness, a concern.",
                         evidence_ids=[e1.id], confidence=0.62)

    review = AdversarialReview({"Tactical": _Bull(), "Context": _Bear(lambda: a.claim_id, e2.id)})
    result = review.run("Is Alpha stronger?", ctx, "digest", {e1.id, e2.id}, max_sweeps=3)
    assert result.sweeps  # at least one sweep ran

    plan = ResearchPlan(question="Is Alpha stronger?", question_type="comparison")
    cons = resolve_contradictions(ctx)
    decision, agg = decide("Is Alpha stronger?", ctx, plan, cons, None,
                           open_cruxes=find_cruxes(ctx), use_llm=False)
    assert decision.answer
    assert decision.confidence_kind == "judgmental"  # a comparison verdict, not a forecast
    assert decision.unresolved_dissent             # surviving disagreement is mandatory

    card = evaluate(decision, ctx, question_type=plan.question_type,
                    aggregate_confidence=agg.decision_confidence,
                    expected_dissent_ids={d.claim_id for d in agg.dissent}, sufficiency=None)
    assert card.passed


# --- lifecycle: shared re-entry counter + budget -----------------------------


def test_runstate_shared_reentry_and_budget():
    state = RunState(run_id="r", max_reentries=2, max_retries=1, budget=Budget(ceiling=100))
    assert state.can_reenter()
    state.reentry_cycle = 2
    assert not state.can_reenter()         # shared counter exhausted
    state.reentry_cycle = 0
    state.budget.debit(100)
    assert not state.can_reenter()         # global budget exhausted gates re-entry too
    assert not state.can_retry()


# --- determinism: the math spine replays bit-for-bit -------------------------


def test_decision_replays_deterministically():
    """Confidence, aggregation, sensitivity and contradiction ranking replay
    identically on frozen evidence (only LLM-authored text would drift)."""
    from server.core.evidence.store import EvidenceContext

    def build():
        c = EvidenceContext("replay")
        c.pool.add(source_tool="quant:strength", args={}, payload={"summary": "x"},
                   strength_tier="authoritative", provenance="quant:strength")  # E01
        c.pool.add(source_tool="get_squad_stats", args={}, payload={},
                   strength_tier="strong", provenance="postgres")               # E02
        a = c.ledger.add_claim(owner="T", dimension="tac",
                               assertion="Alpha are clearly the stronger, dominant side.",
                               evidence_ids=["E01", "E02"], confidence=0.78)
        b = c.ledger.add_claim(owner="C", dimension="ctx",
                               assertion="Alpha carry real risk and weakness, a concern.",
                               evidence_ids=["E02"], confidence=0.6)
        for cl in (a, b):
            c.ledger.set_status(cl.claim_id, "resolved")
        return c

    plan = ResearchPlan(question="q", question_type="comparison")
    runs = []
    for _ in range(3):
        c = build()
        d, _agg = decide("q", c, plan, resolve_contradictions(c), None,
                         open_cruxes=find_cruxes(c), use_llm=False)
        runs.append((d.confidence,
                     tuple(x.claim_id for x in d.unresolved_dissent),
                     tuple(k.crux_id for k in d.open_cruxes)))
    assert runs[0] == runs[1] == runs[2]
