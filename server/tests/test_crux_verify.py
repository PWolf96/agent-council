"""Crux & Sensitivity controller (L4c) + Verifier (L4) — deterministic, evidentiary.

Replaces the v2 prioritize+verify suite: the circular ``impact`` ranking is gone,
so these tests pin the real sensitivity behaviour — *would resolving this claim
flip the answer?* — plus the unchanged evidentiary Verifier.
"""

from server.core.evidence.models import Challenge, ResearchPlan
from server.core.orchestration.crux import find_cruxes, next_action
from server.core.orchestration.verify import verify_challenge


def _claim(ctx, owner, conf, evidence_ids, assertion="a"):
    return ctx.ledger.add_claim(owner=owner, dimension="d", assertion=assertion,
                                evidence_ids=evidence_ids, confidence=conf)


# --- Verifier (unchanged contract) ------------------------------------------


def test_verifier_confirms_contradiction(ctx):
    ctx.store.add(source_tool="claim_ev", args={"x": 1}, payload={}, strength_tier="strong",
                  provenance="postgres")  # E01
    ctx.store.add(source_tool="chal_ev", args={"x": 2}, payload={}, strength_tier="authoritative",
                  provenance="quant:strength")  # E02
    c = _claim(ctx, "O", 0.8, ["E01"])
    ch = Challenge(challenge_id="X1", target_claim=c.claim_id,
                   kind="contradicting_evidence", evidence_ids=["E02"], severity="critical")
    v = verify_challenge(ch, c, ctx.pool)
    assert v.claim_supported and v.challenge_supported and v.contradiction


def test_verifier_drops_uncited_challenge(ctx):
    ctx.store.add(source_tool="claim_ev", args={"x": 1}, payload={}, strength_tier="strong",
                  provenance="postgres")  # E01
    c = _claim(ctx, "O", 0.8, ["E01"])
    ch = Challenge(challenge_id="X1", target_claim=c.claim_id,
                   kind="contradicting_evidence", evidence_ids=[], severity="major")
    v = verify_challenge(ch, c, ctx.pool)
    assert v.challenge_supported is False
    assert v.contradiction is False


def test_verifier_missing_evidence(ctx):
    ctx.store.add(source_tool="claim_ev", args={"x": 1}, payload={}, strength_tier="strong",
                  provenance="postgres")  # E01
    ctx.store.add(source_tool="omitted", args={"x": 9}, payload={}, strength_tier="strong",
                  provenance="postgres")  # E02 - not cited by the claim
    c = _claim(ctx, "O", 0.8, ["E01"])
    ch = Challenge(challenge_id="X1", target_claim=c.claim_id, kind="missing_evidence",
                   evidence_ids=["E02"], severity="major")
    v = verify_challenge(ch, c, ctx.pool)
    assert v.missing_evidence is True


# --- Crux sensitivity (the flip test) ---------------------------------------


def _two_sided(ctx):
    """Opposing reads of shared evidence at a near-even margin."""
    ctx.store.add(source_tool="t", args={"x": 1}, payload={}, strength_tier="strong",
                  provenance="postgres")  # E01
    a = _claim(ctx, "O1", 0.55, ["E01"], "Alpha are clearly the stronger, dominant side.")
    b = _claim(ctx, "O2", 0.50, ["E01"], "Alpha carry real risk and weakness, a concern.")
    return a, b


def test_find_cruxes_flags_pivotal_claim(ctx):
    a, b = _two_sided(ctx)
    cruxes = find_cruxes(ctx)
    pivotal = {c.pivotal_claims[0] for c in cruxes}
    assert a.claim_id in pivotal  # perturbing A's confidence flips the leaning


def test_no_crux_when_one_sided(ctx):
    ctx.store.add(source_tool="t", args={"x": 1}, payload={}, strength_tier="strong",
                  provenance="postgres")  # E01
    _claim(ctx, "O1", 0.7, ["E01"], "Alpha are clearly stronger and dominant.")
    # A lone, one-directional claim cannot flip the decision: not a crux.
    assert find_cruxes(ctx) == []


def test_crux_stable_when_nothing_pivotal(ctx):
    ctx.store.add(source_tool="t", args={"x": 1}, payload={}, strength_tier="strong",
                  provenance="postgres")  # E01
    _claim(ctx, "O1", 0.8, ["E01"], "Alpha are clearly stronger and dominant.")
    plan = ResearchPlan(question="q")
    d = next_action(ctx, plan, sweep=1, max_sweeps=3, reentry_cycle=0, max_reentries=2)
    assert d.action == "stable"


def test_crux_next_sweep_when_pivotal_and_contested(ctx):
    a, _b = _two_sided(ctx)
    ch = ctx.ledger.add_challenge(
        Challenge(challenge_id="", target_claim=a.claim_id, kind="contradicting_evidence",
                  evidence_ids=["E01"], severity="major")
    )
    ch.status = "admitted"  # a live objection on A's current version
    plan = ResearchPlan(question="q")  # no required_evidence -> nothing is "thin"
    # max_reentries=0 disables the re_gather exit, isolating next_sweep.
    d = next_action(ctx, plan, sweep=1, max_sweeps=3, reentry_cycle=0, max_reentries=0)
    assert d.action == "next_sweep"
