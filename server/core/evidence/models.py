"""Typed schemas for the v3 evidence-grounded pipeline.

These are the records that flow through the whole v3 pipeline. They are the
*durable, typed* replacement for v1's free-form prose: instead of paraphrasing
arguments across rounds, the system carries state as cited evidence records in an
immutable **Evidence Pool** and owned, versioned claims in a mutable **Claim
Ledger**.

All models are Pydantic ``BaseModel``s so the LLM-facing ones (``ResearchPlan``,
``ClaimDraft``, ``ChallengeDraft``) can be produced via ``with_structured_output``
and the deterministic ones serialise cleanly into the persisted run transcript.

Vocabulary
----------
* **strength tier** — how much weight a piece of evidence carries in the
  deterministic confidence rule (ordered weakest→strongest). The weights live in
  ``confidence.py``. (These are the design doc's ordered tiers under their
  established runtime names.)
* **source trust** — trust in the *source* (0–1), independent of tier: a
  multiplier in the confidence rule so a noisy source counts for structurally less
  than structured data even at the same tier.
* **confidence kind** — ``calibratable`` (a forecast that resolves and can be
  Brier/log-loss scored) vs ``judgmental`` (a recommendation that never resolves;
  its confidence is a *defensibility score, not P(true)*).
* **claim status** — where a claim sits in its lifecycle (see the state diagram in
  the design doc). Transitions are driven by verified deliberation events.
* **challenge kind / severity** — a challenge must be one of three evidentiary
  kinds; an admissibility gate drops manufactured ("I disagree") objections.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# --- strength tiers ---------------------------------------------------------

# Ordered weakest -> strongest. The weight each tier contributes to a confidence
# update lives in ``confidence.py`` (kept there so the rule is in one place).
StrengthTier = Literal["weak", "moderate", "strong", "authoritative"]

# Where a piece of evidence ranks by default, by the system that produced it.
# Quant models and the structured warehouse are authoritative/strong; the
# qualitative fan-voice store is moderate; anything unclassified is weak.
PROVENANCE_TIER: dict[str, StrengthTier] = {
    "quant": "authoritative",
    "postgres": "strong",
    "qdrant": "moderate",
    "planner": "weak",
}

# Whether a claim/decision's confidence can be scored against a resolving outcome
# (calibratable) or is a defensibility score that never resolves (judgmental).
ConfidenceKind = Literal["calibratable", "judgmental"]

# --- enumerated states ------------------------------------------------------

ClaimStatus = Literal[
    "asserted",      # freshly written by its owner, cited, not yet contested
    "challenged",    # an admitted challenge is open against it
    "revised",       # owner produced a new version in response to scrutiny
    "corroborated",  # independent evidence agreed (confidence rose)
    "refuted",       # owner conceded / could not cite (confidence collapsed)
    "resolved",      # stable: no new admissible challenges remain
]

ChallengeKind = Literal[
    "contradicting_evidence",  # cites evidence that conflicts with the claim
    "missing_evidence",        # flags a decisive fact the claim ignores
    "inference_dispute",       # accepts the evidence, disputes the leap from it
]

ChallengeSeverity = Literal["minor", "major", "critical"]

# Challenge lifecycle: processing states plus the version-binding states. When a
# claim's owner revises to a new version, challenges filed against the prior
# version are marked ``addressed``/``stale`` and do not carry over.
ChallengeStatus = Literal[
    "candidate", "admitted", "verified", "rejected", "dropped", "addressed", "stale"
]

QuestionType = Literal[
    "comparison",   # is A better/stronger than B
    "valuation",    # which player to sign / season quality
    "probability",  # P(A beats B), P(over/under k goals)
    "scouting",     # open-ended player/team assessment
]


# --- evidence ---------------------------------------------------------------


class EvidenceRecord(BaseModel):
    """One immutable, content-addressed fact in the Evidence Pool.

    Every tool result and every quant estimate lands here exactly once, keyed by
    ``args_hash`` (a normalised hash of ``source_tool`` + args + ``as_of``).
    Claims cite these by ``id``; nothing downstream re-fetches what is already
    here, and nothing downstream mutates them.
    """

    id: str
    source_tool: str
    args_hash: str
    args: dict = Field(default_factory=dict)
    payload: Any = None
    retrieved_at: float = 0.0
    strength_tier: StrengthTier = "moderate"
    # Trust in the SOURCE (0-1), independent of tier: a multiplier in the
    # confidence rule so a noisy source counts for structurally less than
    # structured data even at the same tier.
    source_trust: float = 1.0
    provenance: str = ""
    as_of: str | None = None
    # The free-text ``required_evidence`` labels from the plan that this record
    # satisfies — drives the deterministic sufficiency coverage check (L2a).
    covers: list[str] = Field(default_factory=list)
    # True when the underlying tool/quant call returned an error or empty result;
    # such records exist for audit but do not count as coverage.
    is_empty: bool = False
    # True for a "searched, provably absent" finding: a researcher looked and the
    # fact genuinely does not exist. Unlike ``is_empty`` (an error/failure), a
    # negative result RESOLVES a sufficiency slot by absence (a stated limitation),
    # so the gate does not loop forever on a fact that isn't there.
    is_negative_result: bool = False


# --- claims -----------------------------------------------------------------


class ClaimRevision(BaseModel):
    """A prior version of a claim, kept so the full evolution is auditable."""

    version: int
    assertion: str
    confidence: float


class Claim(BaseModel):
    """A typed, owned, versioned, cited assertion — the unit of reasoning.

    Only ``owner`` may mutate a claim (enforced by the ledger). Confidence is
    never set by an LLM: it is recomputed by the deterministic log-odds rule on
    every verified deliberation event, and every value is kept in
    ``confidence_history``.

    There is **no** ``challenges`` field: challenges are separate records pointing
    *in* (reverse lookup via ``ClaimLedger.challenges_for``), version-bound to the
    claim version they were filed against.
    """

    claim_id: str
    owner: str
    dimension: str = ""
    version: int = 1
    assertion: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    confidence_history: list[float] = Field(default_factory=list)
    # calibratable (a forecast that resolves) vs judgmental (a recommendation that
    # does not). Judgmental confidence is a defensibility score, not P(true).
    confidence_kind: ConfidenceKind = "judgmental"
    status: ClaimStatus = "asserted"
    revisions: list[ClaimRevision] = Field(default_factory=list)
    rationale: str = ""
    # Optional claim-dependency edges (a refuted premise could later propagate to
    # dependents); recorded now, propagation is an open decision.
    depends_on: list[str] = Field(default_factory=list)


class ClaimDraft(BaseModel):
    """What a specialist LLM emits per claim (the ledger assigns id/owner/conf)."""

    assertion: str = Field(description="A single, falsifiable domain claim.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="IDs of Evidence Pool records that support this claim.",
    )
    initial_confidence: float = Field(
        default=0.6,
        ge=0.05,
        le=0.95,
        description="Starting confidence in [0.05, 0.95]; later recomputed deterministically.",
    )
    rationale: str = Field(default="", description="One or two sentences of reasoning.")


class SpecialistClaims(BaseModel):
    """Structured-output wrapper: the set of claims one specialist produces."""

    claims: list[ClaimDraft] = Field(default_factory=list)


# --- challenges -------------------------------------------------------------


class Challenge(BaseModel):
    """An evidence-bound objection filed against a specific claim version.

    Filed by ANY specialist (wearing its *critic* hat) — it need not own the
    target. An admissibility gate drops a ``contradicting_evidence`` /
    ``missing_evidence`` objection with no cited evidence and an
    ``inference_dispute`` with no concrete named ``inference_flaw`` before it ever
    costs the owner a turn — this is how manufactured disagreement is made
    impossible.
    """

    challenge_id: str
    target_claim: str
    # The specialist who filed it (any specialist; need NOT own the target). The
    # owner of the target answers it via a Response.
    challenger: str = ""
    kind: ChallengeKind = "inference_dispute"
    evidence_ids: list[str] = Field(default_factory=list)
    # Required (concrete, named) for an inference_dispute: not "I disagree" but the
    # specific flaw in the leap from evidence to assertion.
    inference_flaw: str = ""
    severity: ChallengeSeverity = "minor"
    rationale: str = ""
    # Lifecycle (see ``ChallengeStatus``): processing + version-binding states.
    status: ChallengeStatus = "candidate"
    # The claim version this objection was filed against (version-binding).
    target_version: int = 1


class ChallengeDraft(BaseModel):
    """What a specialist (wearing its *critic* hat) emits per candidate objection."""

    target_claim: str = Field(description="claim_id this objection targets.")
    kind: ChallengeKind = Field(description="The evidentiary kind of the objection.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Evidence IDs that contradict the claim or that it omits.",
    )
    inference_flaw: str = Field(
        default="",
        description="For inference_dispute: the concrete, named flaw in the reasoning.",
    )
    severity: ChallengeSeverity = "minor"
    rationale: str = Field(default="", description="Why this objection holds, citing evidence.")


class ChallengeBatch(BaseModel):
    """Structured-output wrapper: the candidate challenges from one critic pass."""

    challenges: list[ChallengeDraft] = Field(default_factory=list)


# --- owner responses --------------------------------------------------------


class AuthorResponse(BaseModel):
    """How a claim's owner answers a challenge: cite, concede, or revise (LLM draft)."""

    action: Literal["cite", "concede", "revise"] = Field(
        description="cite=defend with stronger evidence; concede=accept; revise=narrow/qualify."
    )
    new_assertion: str = Field(
        default="",
        description="Required when action='revise': the rewritten, narrowed claim.",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Supporting evidence IDs for a 'cite' or the revised claim.",
    )
    rationale: str = Field(default="", description="One or two sentences justifying the action.")


class Response(BaseModel):
    """A stored owner-response record in the ledger (points at a Challenge).

    Separate record type (not a field on the claim): the owner of the target
    claim files exactly one Response per admitted challenge per sweep. Confidence
    is NOT set here — it is recomputed by the confidence service.
    """

    response_id: str = ""
    challenge_id: str
    author: str = ""                      # MUST equal the target claim's owner
    move: Literal["cite", "concede", "revise"] = "cite"
    added_evidence_ids: list[str] = Field(default_factory=list)
    resulting_version: int | None = None  # set when move == 'revise'
    rationale: str = ""


# --- verification -----------------------------------------------------------


class Verification(BaseModel):
    """The Verifier's strictly-evidentiary findings for one challenge.

    The Verifier is *not* a judge: it answers only whether the claim and the
    challenge are each supported by their cited evidence, whether they
    contradict, and whether evidence is missing. Which interpretation is
    ultimately right emerges from owner revisions + the Decision layer.
    """

    challenge_id: str
    target_claim: str
    claim_supported: bool = False
    challenge_supported: bool = False
    contradiction: bool = False
    missing_evidence: bool = False
    note: str = ""


# --- planning ---------------------------------------------------------------


class EvidenceRequest(BaseModel):
    """A single deterministic retrieval instruction produced by the planner.

    The retriever executes each request (a registry tool or a quant model) and
    tags the resulting ``EvidenceRecord`` with this request's ``label`` so the
    sufficiency gate can check coverage label-by-label. ``min_tier`` and
    ``max_age_days`` are the coverage constraints (the design's ``RequiredEvidence``
    aspect): a critical slot cannot be satisfied by a record below ``min_tier`` or
    older than ``max_age_days``.
    """

    label: str = Field(description="Human-readable name of the evidence this fills.")
    # The researcher (L2) responsible for covering this slot. The researcher
    # decides the concrete MCP calls, so ``tool``/``args`` are no longer required
    # at plan time — they are kept (optional) for the retired deterministic path.
    researcher: str = Field(default="", description="Owning researcher key.")
    tool: str = Field(default="", description="Legacy: registry tool / 'quant:<model>'.")
    args: dict = Field(default_factory=dict)
    critical: bool = Field(
        default=False,
        description="If true, a coverage gap here blocks analysis and re-triggers retrieval.",
    )
    # Minimum strength tier that can satisfy this slot (a soft signal cannot cover
    # a critical structured slot). Default "weak" = any non-empty record covers it.
    min_tier: StrengthTier = "weak"
    # Freshness window in days (0 = no freshness constraint).
    max_age_days: int = 0
    as_of: str | None = None


class DeliverableSpec(BaseModel):
    """The *shape* of the answer the question asks for — open-ended, planner-derived.

    This is the "output format" half of the planner's contract, split out from the
    epistemic ``question_type``. It deliberately does **not** enumerate a fixed set
    of shapes: ``format`` is free text the planner writes from the question, so a
    new desired outcome (a ranked list, a table, a tier list, a yes/no) needs *no*
    code change. The Decision layer makes the supporting material fit the shape, and
    the Synthesizer treats satisfying this spec as its primary obligation — always
    grounded in evidence, and honest when the evidence cannot fill the requested
    shape. ``question_type`` decides how confidence is *treated*; this decides how
    the answer is *delivered*.
    """

    # Free-form description of the answer's shape — the single source of truth.
    # e.g. "a ranked list of the top 5 players, each with a grade and the stats
    # behind it" or "a one-line yes/no with the probability".
    format: str = Field(default="a direct, well-grounded recommendation")
    # The subjects the answer must cover/score when they are known up front (e.g.
    # named players/teams). Empty = the subjects emerge from the gathered evidence.
    subjects: list[str] = Field(default_factory=list)
    # How many items the deliverable should contain (e.g. 5 for "top 5"); 0 = N/A.
    cardinality: int = 0
    # The grading/scoring dimensions requested (the user's "grading system"). Empty
    # = no explicit rubric; the analysts' own dimensions stand in.
    dimensions: list[str] = Field(default_factory=list)
    # One line stating what a complete, correct answer to THIS question looks like.
    success_criteria: str = ""

    def is_list_like(self) -> bool:
        """True when the deliverable wants several items (drives breadth at L5)."""
        if self.cardinality > 1 or len(self.subjects) > 1:
            return True
        f = self.format.lower()
        return any(kw in f for kw in ("list", "rank", "top ", "table", "tier", "each "))


class ResearchPlan(BaseModel):
    """L1 output: how to answer this question before anyone reasons."""

    question: str = ""
    question_type: QuestionType = "scouting"
    # The desired answer shape, derived from the question (see ``DeliverableSpec``).
    deliverable: DeliverableSpec = Field(default_factory=DeliverableSpec)
    entities: list[str] = Field(default_factory=list)
    required_evidence: list[EvidenceRequest] = Field(default_factory=list)
    assigned_specialists: list[str] = Field(default_factory=list)
    # L2 researchers selected for this question, and the per-researcher statement
    # ("brief") each one is handed to decide its own MCP calls from.
    assigned_researchers: list[str] = Field(default_factory=list)
    researcher_briefs: dict[str, str] = Field(default_factory=dict)
    quant_models: list[str] = Field(default_factory=list)
    reasoning: str = ""


class SufficiencyReport(BaseModel):
    """L2a output: can the gathered evidence actually answer the question?"""

    sufficient: bool = True
    covered: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    blocking: bool = False
    note: str = ""


# --- orchestration: roster + re-entry + crux + rubric -----------------------


class RosterSelection(BaseModel):
    """L0 output: the experts + researchers admitted for this question.

    Selection is conservative (err toward inclusion on borderline agents) and
    re-admittable (a later re-gather can pull a dropped agent back in).
    """

    question_type: QuestionType = "scouting"
    admitted_specialists: list[str] = Field(default_factory=list)
    admitted_researchers: list[str] = Field(default_factory=list)
    # [{"agent": key, "reason": str}] — logged, re-admittable.
    dropped: list[dict] = Field(default_factory=list)


class RetrievalRequest(BaseModel):
    """A re-gather instruction routed through the single planner re-entry point.

    Both the Sufficiency Gate (critical gap) and the Crux controller (pivotal,
    under-evidenced) and the Evaluator (completeness) emit these. ``cycle`` is the
    single shared counter the re-entry loops share, capped by the Orchestrator.
    """

    origin: Literal["sufficiency_gate", "crux", "evaluator"]
    reason: str = ""
    # Missing evidence keys (plan labels) to (re)gather.
    labels: list[str] = Field(default_factory=list)
    # Concrete requests, when the originator can name them.
    required_evidence: list[EvidenceRequest] = Field(default_factory=list)
    triggering_crux: str | None = None
    cycle: int = 0


class Crux(BaseModel):
    """A claim (or set) whose value flips the decision's argmax answer.

    The Crux controller (L4c) is the inner-loop controller; a crux is what it finds
    when it perturbs uncertain claims and the answer moves.
    """

    crux_id: str = ""
    description: str = ""
    pivotal_claims: list[str] = Field(default_factory=list)
    decision_flips_if: str = ""
    unresolved: bool = True
    # Plan labels to re-gather when the pivotal uncertainty needs new evidence.
    retrieval_request: list[str] = Field(default_factory=list)


class RubricReport(BaseModel):
    """L6 output: the Evaluator's verdict on the finished decision."""

    passed: bool = True
    grounding_ok: bool = True
    calibration_ok: bool = True
    completeness_ok: bool = True
    failure: Literal["grounding", "calibration", "completeness"] | None = None
    # Missing evidence labels when completeness fails.
    missing: list[str] = Field(default_factory=list)
    notes: str = ""


# --- contradiction resolution + decision ------------------------------------


class ContradictionOutcome(BaseModel):
    """L5a output for one conflicting claim pair."""

    claim_a: str
    claim_b: str
    resolution: Literal["reconciled", "dominant", "unresolved"]
    winner: str | None = None  # the dominant claim_id when resolution='dominant'
    reason: str = ""


class Dissent(BaseModel):
    """A surviving disagreement carried into the final decision."""

    claim_id: str
    owner: str
    summary: str


class Decision(BaseModel):
    """L5 output: the calibrated recommendation with mandatory surviving dissent."""

    answer: str = ""
    confidence: float = 0.5
    # Whether this answer's confidence can be calibrated against an outcome.
    confidence_kind: ConfidenceKind = "judgmental"
    supporting_claims: list[str] = Field(default_factory=list)
    unresolved_dissent: list[Dissent] = Field(default_factory=list)
    # Pivotal questions still open at decision time (from the Crux controller).
    open_cruxes: list[Crux] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
