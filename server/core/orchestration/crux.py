"""Crux & Sensitivity controller (L4c) — the inner-loop controller.

Replaces v2's "Challenge Prioritization" and its partly-circular ``impact``
metric. Its question is *"is deliberation done?"* — answered not by counting
objections but by asking the only thing that matters: **would resolving this
disagreement change the answer?**

After each adversarial sweep it runs a **sensitivity analysis** on the decision
aggregation: it perturbs each uncertain claim's confidence across its plausible
range and checks whether the decision's argmax answer (or overall leaning) flips.
Claims that *can* flip the answer are the **cruxes**. Per pass it has exactly
three exits:

* **next_sweep** — a pivotal disagreement remains AND is resolvable from current
  evidence;
* **re_gather** — the pivotal uncertainty is thinly evidenced; emit a
  ``RetrievalRequest`` (the missing critical labels) back through the single
  planner re-entry;
* **stable** — nothing pivotal remains, OR the budget is spent → forward.

This gives a principled early stop: *stop when no admitted objection could flip
the answer.* The perturbation math is deterministic; an LLM is never needed here.
A first implementation may approximate the flip test by ``confidence-swing ×
claim-weight`` ranking — the structure below does the real perturbation but the
two agree on the easy cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.core.evidence.confidence import (
    TIER_WEIGHTS,
    corroboration,
    refutation,
    update_confidence,
)
from server.core.evidence.models import Crux, ResearchPlan, RetrievalRequest
from server.core.evidence.store import EvidenceContext
from server.core.orchestration.contradiction import _polarity as _claim_polarity
from server.core.orchestration.verify import best_tier

# Tier ordering for the "thinly evidenced" test (re-gather trigger).
_TIER_RANK = {"weak": 0, "moderate": 1, "strong": 2, "authoritative": 3}


@dataclass
class CruxDecision:
    """The controller's verdict for one pass: which exit, plus the cruxes found."""

    action: str  # "next_sweep" | "re_gather" | "stable"
    cruxes: list[Crux] = field(default_factory=list)
    retrieval_request: RetrievalRequest | None = None
    reason: str = ""


def _weight(claim, ctx: EvidenceContext) -> float:
    return TIER_WEIGHTS.get(best_tier(ctx.pool, claim.evidence_ids), TIER_WEIGHTS["weak"])


def _statistic(confidences: dict[str, float], claims, ctx: EvidenceContext) -> tuple[int, str]:
    """The decision statistic the flip test watches: (leaning sign, argmax claim).

    * leaning sign — sign of Σ polarity(assertion) · confidence · weight (is the
      overall read bullish or bearish?);
    * argmax claim — the single best-supported claim (the headline driver).

    A crux is a claim whose plausible perturbation changes either of these.
    """
    net = 0.0
    best_id, best_score = "", -1.0
    for c in claims:
        w = _weight(c, ctx)
        conf = confidences[c.claim_id]
        net += _claim_polarity(c.assertion) * conf * w
        score = conf * w
        if score > best_score:
            best_id, best_score = c.claim_id, score
    sign = 1 if net > 1e-9 else (-1 if net < -1e-9 else 0)
    return sign, best_id


def find_cruxes(ctx: EvidenceContext) -> list[Crux]:
    """Claims whose plausible confidence swing flips the decision statistic."""
    claims = [c for c in ctx.ledger.claims() if c.status != "refuted"]
    if len(claims) < 1:
        return []
    base_conf = {c.claim_id: c.confidence for c in claims}
    base_stat = _statistic(base_conf, claims, ctx)

    cruxes: list[Crux] = []
    for c in claims:
        tier = best_tier(ctx.pool, c.evidence_ids)
        low = update_confidence(c.confidence, [refutation(tier)])
        high = update_confidence(c.confidence, [corroboration(tier)])
        flipped = False
        for probe in (low, high):
            trial = dict(base_conf)
            trial[c.claim_id] = probe
            if _statistic(trial, claims, ctx) != base_stat:
                flipped = True
                break
        if flipped:
            cruxes.append(
                Crux(
                    crux_id=f"K-{c.claim_id}",
                    description=f"{c.claim_id} ({c.owner}) is pivotal: {c.assertion[:80]}",
                    pivotal_claims=[c.claim_id],
                    decision_flips_if=(
                        f"confidence in {c.claim_id} moves outside "
                        f"[{round(low, 2)}, {round(high, 2)}]"
                    ),
                    unresolved=bool(ctx.ledger.open_challenges_for(c.claim_id))
                    or 0.3 < c.confidence < 0.7,
                )
            )
    return cruxes


def _thin_critical_labels(plan: ResearchPlan, ctx: EvidenceContext) -> list[str]:
    """Critical plan labels whose best covering evidence is below 'strong'."""
    thin: list[str] = []
    records = ctx.pool.all()
    for req in plan.required_evidence:
        if not req.critical:
            continue
        covering = [r for r in records if req.label in r.covers and not r.is_empty]
        best = max((_TIER_RANK.get(r.strength_tier, 0) for r in covering), default=-1)
        if best < _TIER_RANK["strong"]:
            thin.append(req.label)
    return thin


def next_action(
    ctx: EvidenceContext,
    plan: ResearchPlan,
    *,
    sweep: int,
    max_sweeps: int,
    reentry_cycle: int,
    max_reentries: int,
) -> CruxDecision:
    """Pick the inner-loop exit after a sweep: next_sweep / re_gather / stable."""
    cruxes = find_cruxes(ctx)
    if not cruxes:
        return CruxDecision(action="stable", cruxes=[], reason="no pivotal claim could flip the answer")

    # Is any crux actively contested (resolvable by another sweep)?
    has_open = any(
        ctx.ledger.open_challenges_for(cid)
        for crux in cruxes
        for cid in crux.pivotal_claims
    )
    # Is any crux thinly evidenced (needs evidence not yet strong in the pool)?
    thin = _thin_critical_labels(plan, ctx)

    if thin and reentry_cycle < max_reentries:
        for crux in cruxes:
            crux.retrieval_request = thin
        return CruxDecision(
            action="re_gather",
            cruxes=cruxes,
            retrieval_request=RetrievalRequest(
                origin="crux",
                reason=f"pivotal claim(s) thinly evidenced; re-gather {thin}",
                labels=thin,
                required_evidence=[r for r in plan.required_evidence if r.label in set(thin)],
                triggering_crux=cruxes[0].crux_id,
                cycle=reentry_cycle + 1,
            ),
            reason="pivotal uncertainty needs stronger evidence",
        )

    if has_open and sweep < max_sweeps:
        return CruxDecision(action="next_sweep", cruxes=cruxes,
                            reason="pivotal disagreement remains and is resolvable from the pool")

    return CruxDecision(action="stable", cruxes=cruxes,
                        reason="pivotal but not further resolvable (carried as open cruxes)")
