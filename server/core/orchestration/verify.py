"""Verifier (L4) — strictly evidentiary, *not* a judge.

The Verifier answers only four mechanical questions about a (claim, challenge)
pair, all by checking the Evidence Store:

* Is the **claim** supported by its cited evidence? (its ids exist and at least
  one is non-empty)
* Is the **challenge** supported by its cited evidence? (likewise, by kind)
* Is there a **contradiction** between them?
* Is **evidence missing**?

It never decides which interpretation is ultimately correct — that emerges from
the owner's revisions and the Decision layer's confidence-weighted aggregation.
Because it is pure citation-checking, it is implemented as deterministic rules
(the design's "rule-based + small-model fallback", rule path).
"""

from __future__ import annotations

from server.core.evidence.models import Challenge, Claim, StrengthTier, Verification
from server.core.evidence.store import EvidenceStore

# Strongest-first, so "best tier among cited records" is a simple max.
_TIER_ORDER: dict[StrengthTier, int] = {
    "weak": 0,
    "moderate": 1,
    "strong": 2,
    "authoritative": 3,
}


def _non_empty_ids(store: EvidenceStore, ids: list[str]) -> list[str]:
    out = []
    for i in ids:
        rec = store.get(i)
        if rec is not None and not rec.is_empty:
            out.append(i)
    return out


def best_tier(store: EvidenceStore, ids: list[str], default: StrengthTier = "moderate") -> StrengthTier:
    """Strongest strength tier among the non-empty cited records."""
    tiers = [store.get(i).strength_tier for i in _non_empty_ids(store, ids)]
    if not tiers:
        return default
    return max(tiers, key=lambda t: _TIER_ORDER.get(t, 0))


def tier_and_trust(
    store: EvidenceStore, ids: list[str], default: StrengthTier = "moderate"
) -> tuple[StrengthTier, float]:
    """Strength tier AND source_trust of the strongest non-empty cited record.

    Threaded into the confidence rule so a high-tier-but-low-trust source (e.g. a
    modelled estimate vs raw fan sentiment) moves confidence proportionally less.
    """
    records = [store.get(i) for i in _non_empty_ids(store, ids)]
    if not records:
        return default, 1.0
    best = max(records, key=lambda r: _TIER_ORDER.get(r.strength_tier, 0))
    return best.strength_tier, best.source_trust


def verify_challenge(challenge: Challenge, claim: Claim, store: EvidenceStore) -> Verification:
    """Run the four evidentiary checks for one challenge against one claim."""
    claim_supported = bool(_non_empty_ids(store, claim.evidence_ids))

    if challenge.kind in ("contradicting_evidence", "missing_evidence"):
        challenge_supported = bool(_non_empty_ids(store, challenge.evidence_ids))
    else:  # inference_dispute: a reasoning critique, supported if it has substance
        challenge_supported = bool((challenge.rationale or "").strip())

    contradiction = (
        challenge.kind == "contradicting_evidence"
        and claim_supported
        and challenge_supported
    )
    missing = (
        challenge.kind == "missing_evidence"
        and challenge_supported
        # The flagged evidence is genuinely outside what the claim cited.
        and any(e not in claim.evidence_ids for e in _non_empty_ids(store, challenge.evidence_ids))
    )

    note_parts = []
    if not claim_supported:
        note_parts.append("claim has no non-empty cited evidence")
    if not challenge_supported:
        note_parts.append("challenge not evidence-backed")
    if contradiction:
        note_parts.append("evidentiary contradiction confirmed")
    if missing:
        note_parts.append("claim omits cited decisive evidence")

    return Verification(
        challenge_id=challenge.challenge_id,
        target_claim=claim.claim_id,
        claim_supported=claim_supported,
        challenge_supported=challenge_supported,
        contradiction=contradiction,
        missing_evidence=missing,
        note="; ".join(note_parts) or "no evidentiary issue found",
    )
