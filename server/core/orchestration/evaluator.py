"""Evaluator (L6) — grade the finished decision, route a bounded retry.

Distinct from the per-claim Verifier (L4): the Verifier audits a single claim
against its evidence; the Evaluator grades the **whole decision** against a rubric,
then routes a typed retry on failure. It is **mostly code**; a small temp-0 model
is used only for "does the answer actually address the question?" (optional).

Rubric:

* **grounding** — every supporting claim resolves to a real, non-refuted, *cited*
  claim, and every citation exists in the pool (no Synthesizer smuggling).
* **calibration** — the stated confidence matches the deterministic aggregation,
  the dissent list matches the survivors, and ``confidence_kind`` matches the
  question type (a defensibility score is not labelled calibratable).
* **completeness** — blocking evidence gaps were filled or surfaced as stated
  limitations; unresolved cruxes are carried, not dropped.

On failure the retry is routed by failure type (owned by the Orchestrator):
``grounding`` → re-run the Synthesizer only; ``calibration`` → re-run the
Aggregator; ``completeness`` → re-enter the Planner with a ``RetrievalRequest``.
Bounded by a max-retry cap; on cap the run finalises with the rubric failure as a
stated limitation (the honesty valve).
"""

from __future__ import annotations

from server.core.evidence.models import (
    ConfidenceKind,
    Decision,
    QuestionType,
    RubricReport,
    SufficiencyReport,
)
from server.core.evidence.store import EvidenceContext

# Where each failure routes (owned by the Orchestrator's lifecycle).
RETRY_TARGET = {
    "grounding": "synthesizer",   # re-narrate from resolved claims only
    "calibration": "aggregator",  # re-run the deterministic math
    "completeness": "planner",    # re-enter retrieval for the missing slot
}


def expected_confidence_kind(question_type: QuestionType) -> ConfidenceKind:
    """Probability questions resolve (calibratable); everything else is judgmental."""
    return "calibratable" if question_type == "probability" else "judgmental"


def _grounding_ok(decision: Decision, ctx: EvidenceContext) -> tuple[bool, str]:
    for cid in decision.supporting_claims:
        claim = ctx.ledger.get_claim(cid)
        if claim is None or claim.status == "refuted":
            return False, f"supporting claim {cid} is missing or refuted"
        cited = [e for e in claim.evidence_ids if (r := ctx.pool.get(e)) and not r.is_empty]
        if not cited:
            return False, f"supporting claim {cid} has no live citation"
    for e in decision.citations:
        if not ctx.pool.exists(e):
            return False, f"answer cites unknown evidence {e}"
    return True, "every statement traces to a resolved, cited claim"


def _calibration_ok(
    decision: Decision,
    aggregate_confidence: float,
    expected_dissent_ids: set[str],
    question_type: QuestionType,
) -> tuple[bool, str]:
    if abs(decision.confidence - round(aggregate_confidence, 4)) > 1e-6:
        return False, (
            f"stated confidence {decision.confidence} != aggregation {aggregate_confidence}"
        )
    got = {d.claim_id for d in decision.unresolved_dissent if d.claim_id != "-"}
    if got != {c for c in expected_dissent_ids if c != "-"}:
        return False, "dissent list does not match the surviving disagreement"
    if decision.confidence_kind != expected_confidence_kind(question_type):
        return False, (
            f"confidence_kind {decision.confidence_kind!r} mislabels a "
            f"{question_type} answer"
        )
    return True, "confidence + dissent match the deterministic aggregation"


def _completeness_ok(
    decision: Decision, sufficiency: SufficiencyReport | None
) -> tuple[bool, str, list[str]]:
    if sufficiency is None:
        return True, "no sufficiency report to check", []
    # Every still-missing critical/limitation label must surface somewhere the
    # reader can see it: the dissent list (stated limitations) or open cruxes.
    surfaced = " ".join(
        [d.summary for d in decision.unresolved_dissent]
        + [c.description for c in decision.open_cruxes]
    )
    unsurfaced = [m for m in sufficiency.missing_evidence if m not in surfaced]
    if sufficiency.blocking and unsurfaced:
        return False, f"blocking gaps not surfaced as limitations: {unsurfaced}", unsurfaced
    return True, "blocking gaps were filled or surfaced as limitations", []


def evaluate(
    decision: Decision,
    ctx: EvidenceContext,
    *,
    question_type: QuestionType,
    aggregate_confidence: float,
    expected_dissent_ids: set[str],
    sufficiency: SufficiencyReport | None = None,
) -> RubricReport:
    """Grade the decision; return a structured rubric report (deterministic)."""
    grounding_ok, g_note = _grounding_ok(decision, ctx)
    calibration_ok, c_note = _calibration_ok(
        decision, aggregate_confidence, expected_dissent_ids, question_type
    )
    completeness_ok, p_note, missing = _completeness_ok(decision, sufficiency)

    # First failure (in rubric order) is the one we route on.
    failure = None
    if not grounding_ok:
        failure = "grounding"
    elif not calibration_ok:
        failure = "calibration"
    elif not completeness_ok:
        failure = "completeness"

    return RubricReport(
        passed=failure is None,
        grounding_ok=grounding_ok,
        calibration_ok=calibration_ok,
        completeness_ok=completeness_ok,
        failure=failure,  # type: ignore[arg-type]
        missing=missing,
        notes="; ".join([g_note, c_note, p_note]),
    )


def retry_target(report: RubricReport) -> str | None:
    """Which stage a failed rubric re-enters (None when it passed)."""
    if report.passed or report.failure is None:
        return None
    return RETRY_TARGET[report.failure]
