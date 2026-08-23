"""Decision layer (L5) — confidence-weighted aggregation + thin synthesis.

Consensus-as-agreement is gone. The loop terminates on *evidentiary stability*;
this layer then aggregates **confidence-weighted claims** (not binary outcomes)
into a calibrated recommendation, and **always** surfaces surviving disagreement.

Two halves, mirroring the split of v1's judge:

* **Aggregator (deterministic).** All the math is code: decision confidence is a
  claim-weighted blend (each claim weighted by its evidence strength × an
  optional multi-criteria dimension weight, reusing v1's optional-weights idea),
  supporting claims and citations are selected, and dissent is assembled from
  unresolved contradictions, still-challenged claims, refuted claims, and the
  sufficiency gate's stated limitations. The dissent list is **mandatory** — an
  empty list must mean the run genuinely produced none.
* **Synthesizer (LLM).** Narrates the rationale from the resolved ledger only.
  For probability questions the headline answer *is* the quant number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.core.agents.general.synthesizer import synthesize
from server.core.evidence.confidence import TIER_WEIGHTS, clamp
from server.core.evidence.models import (
    Claim,
    ContradictionOutcome,
    Crux,
    Decision,
    DeliverableSpec,
    Dissent,
    ResearchPlan,
    SufficiencyReport,
)
from server.core.evidence.store import EvidenceContext
from server.core.orchestration.verify import best_tier


@dataclass
class EntityScore:
    """A per-subject confidence-weighted score, for list/ranking deliverables."""

    entity: str
    score: float
    claims: list[Claim] = field(default_factory=list)


@dataclass
class DecisionAggregate:
    """Everything the deterministic aggregator computes, pre-narration."""

    decision_confidence: float
    supporting_claims: list[Claim] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    dissent: list[Dissent] = field(default_factory=list)
    quant_headlines: list[str] = field(default_factory=list)
    leaning: str = ""
    # Subjects scored + ranked when the deliverable wants several items (e.g. a
    # "top 5"). Empty for single-verdict deliverables, or when no subjects were
    # named and none could be matched against the claims.
    entity_scores: list[EntityScore] = field(default_factory=list)


def _claim_weight(claim: Claim, ctx: EvidenceContext, dimension_weights: dict[str, float]) -> float:
    """Evidence strength × multi-criteria dimension weight."""
    tier = best_tier(ctx.store, claim.evidence_ids)
    strength = TIER_WEIGHTS.get(tier, TIER_WEIGHTS["weak"])
    dim_w = dimension_weights.get(claim.dimension, 1.0) if dimension_weights else 1.0
    return strength * dim_w


def _entity_scores(
    surviving: list[Claim],
    ctx: EvidenceContext,
    subjects: list[str],
    dimension_weights: dict[str, float],
) -> list[EntityScore]:
    """Score each named subject by its own confidence-weighted claims, ranked.

    This is what makes a "top N" deliverable possible: instead of collapsing the
    whole ledger to a single strongest claim, every subject gets a score from the
    claims that mention it, so the Synthesizer can render a real ranking. A claim
    is attributed to a subject by a case-insensitive name match in its assertion
    (a claim may count toward more than one subject). Subjects with no matching
    claim are dropped rather than scored at zero — absence of evidence is surfaced
    as dissent elsewhere, not as a misleading bottom rank.
    """
    scored: list[EntityScore] = []
    for subj in subjects:
        needle = subj.lower().strip()
        if not needle:
            continue
        matched = [c for c in surviving if needle in c.assertion.lower()]
        if not matched:
            continue
        num = den = 0.0
        for c in matched:
            w = _claim_weight(c, ctx, dimension_weights)
            num += w * c.confidence
            den += w
        score = clamp(num / den) if den else 0.0
        ranked_claims = sorted(matched, key=lambda c: c.confidence, reverse=True)
        scored.append(EntityScore(entity=subj, score=round(score, 4), claims=ranked_claims))
    scored.sort(key=lambda e: e.score, reverse=True)
    return scored


def _retrieval_health(ctx: EvidenceContext) -> tuple[int, int, int, str]:
    """Classify the evidence pool: ``(usable, errored, negative, sample_error)``.

    Lets the decision distinguish "tools failed to look" (errored — retryable, a
    real gap) from "looked and found nothing" (negative — provable absence) from
    usable evidence, so an empty ledger doesn't get narrated as a reasoned
    non-answer when the real cause was a wall of tool errors.
    """
    usable = errored = negative = 0
    sample = ""
    for rec in ctx.store.all():
        payload = rec.payload
        if isinstance(payload, dict) and payload.get("error"):
            errored += 1
            if not sample:
                sample = str(payload.get("error"))[:160]
        elif rec.is_negative_result:
            negative += 1
        elif not rec.is_empty:
            usable += 1
    return usable, errored, negative, sample


def _quant_headlines(ctx: EvidenceContext) -> list[str]:
    out = []
    for rec in ctx.store.all():
        if rec.is_empty or not rec.provenance.startswith("quant:"):
            continue
        if isinstance(rec.payload, dict) and rec.payload.get("summary"):
            out.append(str(rec.payload["summary"]))
    return out


def aggregate(
    ctx: EvidenceContext,
    plan: ResearchPlan,
    contradictions: list[ContradictionOutcome],
    sufficiency: SufficiencyReport | None = None,
    *,
    dimension_weights: dict[str, float] | None = None,
) -> DecisionAggregate:
    """Deterministically fold the resolved ledger into a decision aggregate."""
    claims = ctx.ledger.claims()
    surviving = [c for c in claims if c.status != "refuted"]

    # Confidence-weighted decision confidence over surviving claims.
    num = den = 0.0
    for c in surviving:
        w = _claim_weight(c, ctx, dimension_weights or {})
        num += w * c.confidence
        den += w
    decision_confidence = round(clamp(num / den) if den else 0.5, 4)

    # Supporting claims: the best-supported surviving claims, strongest first.
    ranked = sorted(
        surviving,
        key=lambda c: (c.confidence * _claim_weight(c, ctx, dimension_weights or {})),
        reverse=True,
    )
    # Breadth follows the deliverable: a single-verdict answer needs only the
    # strongest few claims, but a list/ranking needs enough to cover every item —
    # the old fixed cap of 6 is exactly what collapsed "top 5" onto one subject.
    deliverable = plan.deliverable or DeliverableSpec()
    if deliverable.is_list_like():
        support_limit = max(deliverable.cardinality * 3, 15)
    else:
        support_limit = 6
    supporting = ranked[:support_limit]
    citations: list[str] = []
    for c in supporting:
        for e in c.evidence_ids:
            if e not in citations:
                citations.append(e)

    # Dissent (mandatory if any genuine disagreement survived):
    dissent: list[Dissent] = []
    seen: set[str] = set()

    def add_dissent(claim_id: str, summary: str) -> None:
        claim = ctx.ledger.get_claim(claim_id)
        owner = claim.owner if claim else "?"
        key = f"{claim_id}:{summary[:40]}"
        if key in seen:
            return
        seen.add(key)
        dissent.append(Dissent(claim_id=claim_id, owner=owner, summary=summary))

    for con in contradictions:
        if con.resolution == "unresolved":
            add_dissent(con.claim_a, f"Unresolved conflict with {con.claim_b}: {con.reason}")
        elif con.resolution == "dominant" and con.winner:
            loser = con.claim_b if con.winner == con.claim_a else con.claim_a
            loser_claim = ctx.ledger.get_claim(loser)
            if loser_claim is not None:
                add_dissent(
                    loser, f"Out-weighed by {con.winner} but stands as a minority read: "
                    f"{loser_claim.assertion}"
                )
    for c in surviving:
        if c.status == "challenged":
            add_dissent(c.claim_id, f"Remains contested after scrutiny: {c.assertion}")
    for c in claims:
        if c.status == "refuted":
            add_dissent(c.claim_id, f"Refuted in deliberation (recorded): {c.assertion}")

    # Sufficiency limitations become first-class dissent, not silent omissions.
    if sufficiency and sufficiency.missing_evidence:
        dissent.append(
            Dissent(
                claim_id="-",
                owner="Evidence Reviewer",
                summary=f"Stated limitation — evidence not gathered: {sufficiency.missing_evidence}",
            )
        )

    # When no usable evidence landed, name the cause so the answer doesn't read as
    # a reasoned "cannot be determined". Tool errors (a failure to look) are
    # distinct from negative results (looked, provably absent).
    usable, errored, negative, sample_error = _retrieval_health(ctx)
    if usable == 0:
        if errored:
            dissent.append(
                Dissent(
                    claim_id="-",
                    owner="Evidence Reviewer",
                    summary=(
                        f"Retrieval failure — {errored} tool call(s) errored and no usable "
                        f"evidence was gathered, so this reflects missing data, not its "
                        f"absence (retryable). Sample error: {sample_error}"
                    ),
                )
            )
        elif negative:
            dissent.append(
                Dissent(
                    claim_id="-",
                    owner="Evidence Reviewer",
                    summary=(
                        f"No evidence found — {negative} tool call(s) searched and returned "
                        f"nothing (provable absence, not a tool failure)."
                    ),
                )
            )

    # Per-subject ranking when the deliverable is list-shaped. Subjects come from
    # the planner (named candidates) or fall back to the plan entities; if neither
    # is known the ranking is left empty and the Synthesizer works from the widened
    # supporting-claim set directly.
    entity_scores: list[EntityScore] = []
    if deliverable.is_list_like():
        subjects = deliverable.subjects or plan.entities
        entity_scores = _entity_scores(surviving, ctx, subjects, dimension_weights or {})

    leaning = supporting[0].assertion if supporting else "No supported claim survived."
    return DecisionAggregate(
        decision_confidence=decision_confidence,
        supporting_claims=supporting,
        citations=citations,
        dissent=dissent,
        quant_headlines=_quant_headlines(ctx),
        leaning=leaning,
        entity_scores=entity_scores,
    )


def _render_deliverable(deliverable: DeliverableSpec) -> list[str]:
    """Render the requested answer shape so the Synthesizer can honour it."""
    lines = ["deliverable (the answer MUST take this shape):", f"  format: {deliverable.format}"]
    if deliverable.cardinality:
        lines.append(f"  item_count: {deliverable.cardinality}")
    if deliverable.subjects:
        lines.append(f"  subjects: {', '.join(deliverable.subjects)}")
    if deliverable.dimensions:
        lines.append(f"  grading_dimensions: {', '.join(deliverable.dimensions)}")
    if deliverable.success_criteria:
        lines.append(f"  success_criteria: {deliverable.success_criteria}")
    return lines


def _render_aggregate(question: str, agg: DecisionAggregate, plan: ResearchPlan) -> str:
    lines = [f"question_type: {plan.question_type}", f"decision_confidence: {agg.decision_confidence}"]
    lines += _render_deliverable(plan.deliverable or DeliverableSpec())
    if agg.quant_headlines:
        lines.append("quant_forecasts:")
        lines += [f"  - {h}" for h in agg.quant_headlines]
    if agg.entity_scores:
        lines.append("entity_rankings (deterministic, strongest first — use as the spine of the list):")
        for rank, es in enumerate(agg.entity_scores, start=1):
            cids = [c.claim_id for c in es.claims]
            lines.append(f"  {rank}. {es.entity} — score {es.score} (claims {cids})")
    lines.append("supporting_claims (strongest first):")
    for c in agg.supporting_claims:
        lines.append(f"  - [{c.claim_id}] ({c.owner}, conf {c.confidence}, cites {c.evidence_ids}) {c.assertion}")
    if agg.dissent:
        lines.append("unresolved_dissent:")
        for d in agg.dissent:
            lines.append(f"  - ({d.owner}) {d.summary}")
    else:
        lines.append("unresolved_dissent: none")
    return "\n".join(lines)


def decide(
    question: str,
    ctx: EvidenceContext,
    plan: ResearchPlan,
    contradictions: list[ContradictionOutcome],
    sufficiency: SufficiencyReport | None = None,
    *,
    dimension_weights: dict[str, float] | None = None,
    open_cruxes: list[Crux] | None = None,
    synthesizer_model: str = "gpt-4o-mini",
    use_llm: bool = True,
    callbacks: list | None = None,
) -> tuple[Decision, DecisionAggregate]:
    """Produce the final ``Decision`` (deterministic math + narrated answer).

    For probability questions the headline is the calibrated quant number and the
    confidence is ``calibratable``; every other question type is ``judgmental`` —
    a defensibility score, not P(true).
    """
    agg = aggregate(ctx, plan, contradictions, sufficiency, dimension_weights=dimension_weights)
    block = _render_aggregate(question, agg, plan)

    answer = ""
    if use_llm:
        answer = synthesize(
            question, block, deliverable=plan.deliverable,
            model_name=synthesizer_model, callbacks=callbacks,
        )
    if not answer:
        # Deterministic fallback narrative so a run always has an answer.
        answer = _fallback_answer(agg, plan)

    decision = Decision(
        answer=answer,
        confidence=agg.decision_confidence,
        confidence_kind="calibratable" if plan.question_type == "probability" else "judgmental",
        supporting_claims=[c.claim_id for c in agg.supporting_claims],
        unresolved_dissent=agg.dissent,
        open_cruxes=list(open_cruxes or []),
        citations=agg.citations,
    )
    return decision, agg


def _fallback_answer(agg: DecisionAggregate, plan: ResearchPlan) -> str:
    # When the deliverable is a ranking and we have scored subjects, render the
    # list directly so even the no-LLM path returns the requested shape.
    if agg.entity_scores:
        n = (plan.deliverable.cardinality if plan.deliverable else 0) or len(agg.entity_scores)
        rows = [f"{i}. {es.entity} (score {es.score})"
                for i, es in enumerate(agg.entity_scores[:n], start=1)]
        parts = ["Ranked by confidence-weighted evidence:", " ".join(rows),
                 f"Decision confidence {agg.decision_confidence}."]
        if agg.dissent:
            parts.append(f"Surviving dissent: {len(agg.dissent)} item(s).")
        return " ".join(parts)

    parts = [agg.leaning]
    if agg.quant_headlines:
        parts.append("Quant: " + "; ".join(agg.quant_headlines))
    parts.append(f"Decision confidence {agg.decision_confidence}.")
    if agg.dissent:
        parts.append(f"Surviving dissent: {len(agg.dissent)} item(s).")
    return " ".join(parts)
