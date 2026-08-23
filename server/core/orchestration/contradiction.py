"""Contradiction Resolution (L5a) — adjudicate conflicts before synthesis.

The Verifier *identifies* contradictions but deliberately does not *resolve*
them. This deterministic step runs once the loop terminates, so no unresolved
conflict reaches the Decision layer silently.

Two claims **conflict** when different owners read the *same shared evidence* in
opposite directions — the v2 thesis in miniature ("agents debate interpretations
of shared evidence"). Detection is deterministic: a pair that (a) is owned by
different specialists, (b) shares at least one cited evidence id, and (c) has
opposing assertion *polarity* (one bullish, one cautionary) is a conflict.

Each conflict is ranked by **evidence strength → confidence → evidence coverage**
(the design's order). A strict winner is ``dominant``; an exact tie is
``unresolved`` and carried to the decision as dissent. We never force a
reconciliation without an explicit basis (the design's "false reconciliation"
guard), so genuine, irreducible conflicts survive as explicit dissent.
"""

from __future__ import annotations

import re

from server.core.evidence.models import Claim, ContradictionOutcome
from server.core.evidence.store import EvidenceContext
from server.core.orchestration.verify import _TIER_ORDER, best_tier

# Lightweight polarity lexicons. Deterministic and intentionally conservative:
# only clearly-directional language sets a sign; everything else is neutral (0)
# and cannot form a conflict.
_POSITIVE = {
    "elite", "strong", "stronger", "strongest", "best", "superior", "sign", "recommend",
    "excellent", "top", "outperform", "favourite", "favorite", "advantage", "clear",
    "dominant", "quality", "improve", "improving", "rising", "good", "great",
}
_NEGATIVE = {
    "risk", "risky", "weak", "weaker", "weakest", "concern", "concerning", "avoid",
    "overrated", "struggle", "struggling", "doubt", "decline", "declining", "fatigue",
    "injury", "worse", "poor", "fewer", "against", "downside", "vulnerable", "sliding",
    "not",
}


def _polarity(assertion: str) -> int:
    tokens = set(re.findall(r"[a-z']+", assertion.lower()))
    pos = len(tokens & _POSITIVE)
    neg = len(tokens & _NEGATIVE)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


def _coverage(claim: Claim, ctx: EvidenceContext) -> int:
    return len([e for e in claim.evidence_ids if (r := ctx.store.get(e)) and not r.is_empty])


def find_conflicts(ctx: EvidenceContext) -> list[tuple[Claim, Claim]]:
    """Deterministically pair claims that read shared evidence oppositely."""
    claims = [c for c in ctx.ledger.claims() if c.status != "refuted"]
    conflicts: list[tuple[Claim, Claim]] = []
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a, b = claims[i], claims[j]
            if a.owner == b.owner:
                continue
            if not (set(a.evidence_ids) & set(b.evidence_ids)):
                continue
            pa, pb = _polarity(a.assertion), _polarity(b.assertion)
            if pa != 0 and pb != 0 and pa != pb:
                conflicts.append((a, b))
    return conflicts


def _rank_key(claim: Claim, ctx: EvidenceContext) -> tuple[int, float, int]:
    """(evidence strength, confidence, coverage) — higher wins, in that order."""
    tier = best_tier(ctx.store, claim.evidence_ids)
    return (_TIER_ORDER.get(tier, 0), claim.confidence, _coverage(claim, ctx))


def resolve_contradictions(ctx: EvidenceContext) -> list[ContradictionOutcome]:
    """Adjudicate every detected conflict on evidence, not rhetoric."""
    outcomes: list[ContradictionOutcome] = []
    for a, b in find_conflicts(ctx):
        ka, kb = _rank_key(a, ctx), _rank_key(b, ctx)
        if ka == kb:
            outcomes.append(
                ContradictionOutcome(
                    claim_a=a.claim_id, claim_b=b.claim_id, resolution="unresolved",
                    reason=(
                        "Indistinguishable on evidence strength, confidence, and coverage — "
                        "carried forward as genuine dissent."
                    ),
                )
            )
            continue
        winner, loser = (a, b) if ka > kb else (b, a)
        outcomes.append(
            ContradictionOutcome(
                claim_a=a.claim_id, claim_b=b.claim_id, resolution="dominant",
                winner=winner.claim_id,
                reason=(
                    f"{winner.claim_id} ({winner.owner}) outranks {loser.claim_id} "
                    f"on evidence strength→confidence→coverage "
                    f"({_rank_key(winner, ctx)} vs {_rank_key(loser, ctx)})."
                ),
            )
        )
    return outcomes
