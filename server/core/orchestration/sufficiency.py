"""Evidence Sufficiency Gate (L2a) — the retrieval gate, kept deliberately "dumb".

Sits between retrieval and specialist analysis. It stops the system from
reasoning flawlessly on incomplete information: the failure where the loop
produces beautifully refined claims that simply omit a decisive fact (salary
expectations, an injury record, a missing season).

The check is a *deterministic coverage map* against the **declared plan** (it does
not discover new needs mid-flight — that is the Crux's job, L4c). A slot is
**covered** iff:

* a non-empty record exists for its label, **AND** that record clears the slot's
  ``min_tier`` (a soft signal cannot satisfy a critical structured slot), **AND**
  the record is fresh enough for the slot's ``max_age_days``; **OR**
* a **negative-result** record exists for it ("searched, provably absent") — the
  slot is resolved by absence and becomes a stated limitation.

A *critical* slot left uncovered is **blocking** and re-triggers a *targeted*
retrieval of just that label (bounded by a cycle cap); a non-critical gap is
recorded as a known limitation and surfaced later as dissent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.core.evidence.models import (
    EvidenceRecord,
    EvidenceRequest,
    ResearchPlan,
    RetrievalRequest,
    SufficiencyReport,
)
from server.core.evidence.store import EvidenceContext

# Tier ordering (weakest -> strongest) for the ``min_tier`` comparison.
_TIER_RANK: dict[str, int] = {"weak": 0, "moderate": 1, "strong": 2, "authoritative": 3}


def _record_age_days(record: EvidenceRecord, now: datetime) -> float | None:
    """Best-effort age of a record in days, or ``None`` if undeterminable."""
    if record.as_of:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(record.as_of[: len(fmt) + 2], fmt)
                return (now.replace(tzinfo=None) - dt).days
            except ValueError:
                continue
    if record.retrieved_at:
        return (now.timestamp() - record.retrieved_at) / 86400.0
    return None


def _record_satisfies(req: EvidenceRequest, record: EvidenceRecord, now: datetime) -> bool:
    """Does one record cover ``req`` under tier + freshness (or by absence)?"""
    if req.label not in record.covers:
        return False
    # A negative result resolves the slot by absence regardless of tier/age.
    if record.is_negative_result:
        return True
    if record.is_empty:
        return False
    if _TIER_RANK.get(record.strength_tier, 0) < _TIER_RANK.get(req.min_tier, 0):
        return False
    if req.max_age_days > 0:
        age = _record_age_days(record, now)
        if age is not None and age > req.max_age_days:
            return False
    return True


def _coverage(plan: ResearchPlan, ctx: EvidenceContext) -> dict[str, bool]:
    """Map each plan label -> covered? under its own tier/freshness constraints."""
    now = datetime.now(timezone.utc)
    records = ctx.pool.all()
    coverage: dict[str, bool] = {}
    for req in plan.required_evidence:
        coverage[req.label] = any(_record_satisfies(req, r, now) for r in records)
    return coverage


def review_sufficiency(plan: ResearchPlan, ctx: EvidenceContext) -> SufficiencyReport:
    """Map required evidence to what actually landed; report gaps."""
    coverage = _coverage(plan, ctx)

    covered_labels: list[str] = []
    missing_labels: list[str] = []
    critical_missing: list[str] = []

    for req in plan.required_evidence:
        if coverage.get(req.label):
            covered_labels.append(req.label)
        else:
            missing_labels.append(req.label)
            if req.critical:
                critical_missing.append(req.label)

    blocking = bool(critical_missing)
    sufficient = not blocking
    if missing_labels:
        non_critical = [m for m in missing_labels if m not in critical_missing]
        note = (
            f"{len(covered_labels)}/{len(plan.required_evidence)} evidence items covered. "
            + (f"Critical gaps: {critical_missing}. " if critical_missing else "")
            + (f"Non-critical gaps recorded as limitations: {non_critical}." if non_critical else "")
        ).strip()
    else:
        note = "All planned evidence covered."

    return SufficiencyReport(
        sufficient=sufficient,
        covered=covered_labels,
        missing_evidence=missing_labels,
        blocking=blocking,
        note=note,
    )


def critical_gap_labels(plan: ResearchPlan, report: SufficiencyReport) -> set[str]:
    """The critical, still-missing labels — what a retry cycle should re-fetch."""
    critical = {r.label for r in plan.required_evidence if r.critical}
    return critical & set(report.missing_evidence)


def gap_retrieval_request(
    plan: ResearchPlan, report: SufficiencyReport, *, cycle: int
) -> RetrievalRequest:
    """Build the targeted re-gather request for the critical gaps (single re-entry)."""
    gaps = sorted(critical_gap_labels(plan, report))
    return RetrievalRequest(
        origin="sufficiency_gate",
        reason=f"critical evidence slots uncovered: {gaps}",
        labels=gaps,
        required_evidence=[r for r in plan.required_evidence if r.label in set(gaps)],
        cycle=cycle,
    )
