"""Evidence Store + Claim Ledger (L2): dedup, ownership, versioning."""

import pytest

from server.core.evidence.confidence import refutation
from server.core.evidence.models import Challenge
from server.core.evidence.store import OwnershipError, args_hash


def test_evidence_dedup_by_args_hash(ctx):
    r1 = ctx.store.add(source_tool="get_team_form", args={"team": "A", "season": "x"},
                       payload={"v": 1}, strength_tier="strong", provenance="postgres")
    r2 = ctx.store.add(source_tool="get_team_form", args={"team": "A", "season": "x"},
                       payload={"v": 1}, strength_tier="strong", provenance="postgres")
    assert r1.id == r2.id
    assert len(ctx.store) == 1


def test_args_hash_is_order_independent():
    a = args_hash("t", {"x": 1, "y": 2}, None)
    b = args_hash("t", {"y": 2, "x": 1}, None)
    assert a == b


def test_as_of_distinguishes_records(ctx):
    r1 = ctx.store.add(source_tool="t", args={"x": 1}, payload={}, as_of="2025-01-01")
    r2 = ctx.store.add(source_tool="t", args={"x": 1}, payload={}, as_of="2025-06-01")
    assert r1.id != r2.id


def test_covers_merge_on_dedup(ctx):
    ctx.store.add(source_tool="t", args={"x": 1}, payload={}, covers=["a"])
    r = ctx.store.add(source_tool="t", args={"x": 1}, payload={}, covers=["b"])
    assert set(r.covers) == {"a", "b"}


def test_claim_versioning_and_history(ctx):
    c = ctx.ledger.add_claim(owner="O", dimension="d", assertion="v1 text",
                             evidence_ids=["E01"], confidence=0.8)
    assert c.version == 1 and c.confidence_history == [0.8]

    ctx.ledger.apply_events(c.claim_id, [refutation("strong")])
    assert len(c.confidence_history) == 2 and c.confidence < 0.8

    ctx.ledger.revise_claim(c.claim_id, owner="O", new_assertion="v2 text")
    assert c.version == 2 and c.assertion == "v2 text"
    assert c.revisions[0].assertion == "v1 text"
    assert c.status == "revised"


def test_ownership_enforced(ctx):
    c = ctx.ledger.add_claim(owner="Owner", dimension="d", assertion="x",
                             evidence_ids=["E01"], confidence=0.6)
    with pytest.raises(OwnershipError):
        ctx.ledger.revise_claim(c.claim_id, owner="Intruder", new_assertion="hijacked")


def test_challenge_reverse_lookup_and_version_binding(ctx):
    # v3: a critique is a SEPARATE record pointing in — no list on the claim.
    c = ctx.ledger.add_claim(owner="O", dimension="d", assertion="x",
                             evidence_ids=["E01"], confidence=0.6)
    ch = ctx.ledger.add_challenge(
        Challenge(challenge_id="", target_claim=c.claim_id, kind="inference_dispute",
                  inference_flaw="overreaches the cited evidence", rationale="overreach")
    )
    assert ch.challenge_id  # auto-assigned
    # "All critiques of C" is a reverse lookup, not a stored list.
    assert ch.challenge_id in [x.challenge_id for x in ctx.ledger.challenges_for(c.claim_id)]
    assert ch.target_version == c.version
    # Version-binding: revising past the version the challenge was filed against
    # marks it stale, so it does not carry over to text that no longer exists.
    ctx.ledger.revise_claim(c.claim_id, owner="O", new_assertion="x narrowed")
    assert ctx.ledger.get_challenge(ch.challenge_id).status == "stale"
    assert ctx.ledger.open_challenges_for(c.claim_id) == []


def test_snapshot_serialises_state(ctx):
    ctx.store.add(source_tool="t", args={"x": 1}, payload={}, covers=["a"])
    ctx.ledger.add_claim(owner="O", dimension="d", assertion="x",
                         evidence_ids=["E01"], confidence=0.6)
    snap = ctx.snapshot()
    assert len(snap["evidence"]) == 1
    assert len(snap["claims"]) == 1
    assert "challenges" in snap
