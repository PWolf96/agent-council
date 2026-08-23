"""Integration tests.

* ``test_offline_*`` wire L3→L5 with fake LLM agents — no network, fully
  deterministic — to prove the orchestration composes correctly.
* ``test_live_*`` opt in to the gpt-4o-mini model (and, where noted, the live
  ``football-analytics`` MCP server); they skip when that infra is unavailable.
  The pipeline degrades gracefully — unreachable MCP servers become non-critical
  gaps — so an end-to-end run needs only an API key.
"""

from server.core.evidence.models import AuthorResponse, ClaimDraft, ResearchPlan
from server.core.orchestration.analysis import run_specialists
from server.core.orchestration.contradiction import resolve_contradictions
from server.core.orchestration.decision import decide
from server.core.orchestration.deliberation import AdversarialReview
from server.core.orchestration.pipeline import stream_deliberation

from server.tests.conftest import TEST_MODEL, requires_openai


# --- offline L3->L5 wiring ---------------------------------------------------


class _FakeWriter:
    def __init__(self, label, dimension, key, drafts):
        self.label, self.dimension, self.key = label, dimension, key
        self._drafts = drafts

    def write_claims(self, question, digest, valid_ids, callbacks=None):
        return [d for d in self._drafts if set(d.evidence_ids) & valid_ids]

    def respond(self, claim, challenge, digest, valid_ids, callbacks=None):
        return AuthorResponse(action="cite", evidence_ids=list(claim.evidence_ids))


def test_offline_analysis_to_decision(ctx):
    ctx.store.add(source_tool="quant:strength", args={}, payload={"summary": "Alpha stronger"},
                  strength_tier="authoritative", provenance="quant:strength", covers=["q"])  # E01
    ctx.store.add(source_tool="get_squad_stats", args={}, payload={"x": 1},
                  strength_tier="strong", provenance="postgres", covers=["squad"])  # E02

    writers = {
        "tac": _FakeWriter("Tactical", "tactical", "tac", [
            ClaimDraft(assertion="Alpha are clearly the stronger, dominant side.",
                       evidence_ids=["E01", "E02"], initial_confidence=0.8),
        ]),
        "ctx": _FakeWriter("Context", "context", "ctx", [
            ClaimDraft(assertion="Alpha carry fatigue risk, a real weakness and concern.",
                       evidence_ids=["E02"], initial_confidence=0.6),
        ]),
    }
    plan = ResearchPlan(question="Is Alpha stronger?", question_type="comparison",
                        assigned_specialists=["tac", "ctx"])

    claims = run_specialists(plan, ctx, writers)
    assert len(claims) == 2
    assert all(c.evidence_ids for c in claims)  # grounding enforced

    for c in claims:
        ctx.ledger.set_status(c.claim_id, "resolved")
    contradictions = resolve_contradictions(ctx)
    assert any(o.resolution == "dominant" for o in contradictions)

    decision, agg = decide("Is Alpha stronger?", ctx, plan, contradictions, None, use_llm=False)
    assert 0.05 <= decision.confidence <= 0.95
    assert decision.unresolved_dissent  # the out-weighed claim survives as dissent
    assert decision.answer


def test_offline_uncited_claims_dropped(ctx):
    ctx.store.add(source_tool="t", args={}, payload={"x": 1}, strength_tier="strong",
                  provenance="postgres", covers=["a"])  # E01
    writers = {
        "tac": _FakeWriter("Tactical", "tactical", "tac", [
            ClaimDraft(assertion="grounded", evidence_ids=["E01"], initial_confidence=0.7),
            ClaimDraft(assertion="ungrounded", evidence_ids=["E99"], initial_confidence=0.7),
        ]),
    }
    plan = ResearchPlan(question="q", assigned_specialists=["tac"])
    claims = run_specialists(plan, ctx, writers)
    assert len(claims) == 1
    assert claims[0].assertion == "grounded"


# --- live integration --------------------------------------------------------


@requires_openai
def test_live_pipeline_end_to_end():
    # L2 is researcher+MCP driven. Whether the football-analytics server is up or
    # down, the pipeline must run end to end and produce a decision — proving the
    # wiring composes (a down server just yields non-critical "gap" evidence).
    events = list(stream_deliberation(
        "Is Manchester City stronger than Arsenal this season?",
        "football_team_board", session_id="pytest-e2e",
        default_model=TEST_MODEL, max_passes=1,
    ))
    kinds = [e["event"] for e in events]
    assert "plan" in kinds and "sufficiency" in kinds and "decision" in kinds

    # The planner selected researchers and briefed each.
    plan = next(e["data"] for e in events if e["event"] == "plan")
    assert plan["assigned_researchers"]
    assert set(plan["researcher_briefs"]) == set(plan["assigned_researchers"])

    decision = next(e["data"] for e in events if e["event"] == "decision")
    assert 0.0 <= decision["confidence"] <= 0.95
    assert "unresolved_dissent" in decision  # mandatory key
    assert decision["answer"]

    # Researchers fetched via MCP (records carry mcp: provenance, even if empty).
    snapshot = next(e["data"] for e in events if e["event"] == "evidence_snapshot")
    assert all(r["provenance"].startswith("mcp:") for r in snapshot["evidence"])
