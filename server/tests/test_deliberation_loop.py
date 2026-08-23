"""Adversarial review sweep (L4) with fake specialists — single-role, deterministic.

The standalone Challenger is gone: every specialist is both critic (attacks claims
it does not own) and owner (defends its own). These fakes implement ``review`` +
``respond`` to drive the sweep offline.
"""

from server.core.evidence.models import AuthorResponse, ChallengeDraft
from server.core.orchestration.deliberation import AdversarialReview


class _Owner:
    """Owns the seeded claim; revises when challenged. Attacks no one."""

    label = "Tactical"
    dimension = "tactical"
    key = "tac"

    def review(self, q, others, digest, valid_ids, callbacks=None):
        return []

    def respond(self, claim, challenge, digest, valid_ids, callbacks=None):
        return AuthorResponse(action="revise",
                              new_assertion="A is stronger but far from guaranteed.",
                              evidence_ids=list(claim.evidence_ids), rationale="narrowing")


class _ConcedingOwner(_Owner):
    def respond(self, claim, challenge, digest, valid_ids, callbacks=None):
        return AuthorResponse(action="concede", rationale="fair point")


class _Critic:
    """Files one admissible contradiction on sweep 1, then nothing."""

    label = "Context"
    dimension = "context"
    key = "ctx"

    def __init__(self, target_getter, evidence_id):
        self._target = target_getter
        self._ev = evidence_id
        self.calls = 0

    def review(self, q, others, digest, valid_ids, callbacks=None):
        self.calls += 1
        if self.calls == 1:
            return [ChallengeDraft(target_claim=self._target(), kind="contradicting_evidence",
                                   evidence_ids=[self._ev], severity="critical",
                                   rationale="quant contradicts the claim")]
        return []

    def respond(self, claim, challenge, digest, valid_ids, callbacks=None):
        return AuthorResponse(action="cite", evidence_ids=list(claim.evidence_ids))


def _seed(ctx):
    e1 = ctx.store.add(source_tool="get_team_form", args={"t": "A"}, payload={"f": "WWWWW"},
                       strength_tier="strong", provenance="postgres", covers=["A form"])
    e2 = ctx.store.add(source_tool="quant:win_probability", args={}, payload={"summary": "P=0.3"},
                       strength_tier="authoritative", provenance="quant:win_probability",
                       covers=["q"])
    claim = ctx.ledger.add_claim(owner="Tactical", dimension="tactical",
                                 assertion="A is guaranteed to win, clearly dominant.",
                                 evidence_ids=[e1.id], confidence=0.82)
    return e1, e2, claim


def test_sweep_revises_and_drops_confidence(ctx):
    e1, e2, claim = _seed(ctx)
    review = AdversarialReview(
        {"Tactical": _Owner(), "Context": _Critic(lambda: claim.claim_id, e2.id)}
    )
    result = review.run("Is A strongest?", ctx, "digest", {e1.id, e2.id}, max_sweeps=3)

    final = ctx.ledger.get_claim(claim.claim_id)
    assert final.version == 2          # revised once by its owner
    assert final.confidence < 0.82     # scrutiny lowered it
    assert final.confidence_history[0] == 0.82
    assert final.status == "resolved"  # settled after the loop
    assert "fixed point" in result.stop_reason
    assert result.sweeps[0].admitted == 1
    assert result.sweeps[0].revisions == 1


def test_sweep_concede_refutes_claim(ctx):
    e1, e2, claim = _seed(ctx)
    review = AdversarialReview(
        {"Tactical": _ConcedingOwner(), "Context": _Critic(lambda: claim.claim_id, e2.id)}
    )
    review.run("Is A strongest?", ctx, "digest", {e1.id, e2.id}, max_sweeps=3)
    final = ctx.ledger.get_claim(claim.claim_id)
    assert final.status == "refuted"
    assert final.confidence < 0.5


def test_admissibility_drops_manufactured_objection(ctx):
    """An inference_dispute with no concrete flaw is inadmissible (manufactured)."""
    e1, e2, claim = _seed(ctx)

    class _BadCritic(_Critic):
        def review(self, q, others, digest, valid_ids, callbacks=None):
            self.calls += 1
            return [ChallengeDraft(target_claim=claim.claim_id, kind="inference_dispute",
                                   evidence_ids=[], inference_flaw="", rationale="I disagree")]

    review = AdversarialReview(
        {"Tactical": _Owner(), "Context": _BadCritic(lambda: claim.claim_id, e2.id)}
    )
    result = review.run("q", ctx, "digest", {e1.id, e2.id}, max_sweeps=2)

    final = ctx.ledger.get_claim(claim.claim_id)
    assert final.version == 1          # untouched: nothing admissible was ever filed
    assert final.confidence == 0.82
    assert result.sweeps[0].admitted == 0
    assert result.sweeps[0].dropped >= 1


def test_sweep_terminates_at_max_sweeps(ctx):
    e1, e2, claim = _seed(ctx)

    class _Relentless:
        """A new, genuinely-distinct admissible objection every sweep."""

        label = "Context"
        dimension = "context"
        key = "ctx"

        def __init__(self):
            self.n = 0

        def review(self, q, others, digest, valid_ids, callbacks=None):
            self.n += 1
            ev = e1.id if self.n % 2 else e2.id
            target = others[0].claim_id if others else claim.claim_id
            return [ChallengeDraft(target_claim=target, kind="contradicting_evidence",
                                   evidence_ids=[ev], severity="major", rationale=f"clash {self.n}")]

        def respond(self, *a, **k):
            return AuthorResponse(action="cite")

    review = AdversarialReview({"Tactical": _Owner(), "Context": _Relentless()})
    result = review.run("q", ctx, "digest", {e1.id, e2.id}, max_sweeps=2)
    assert len(result.sweeps) == 2  # bounded by the cap
    assert result.stop_reason == "max sweeps reached"
