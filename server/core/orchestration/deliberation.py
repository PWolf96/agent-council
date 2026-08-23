"""Adversarial Review sweep (L4) — the single-role deliberation loop.

This is the heart of v3, and the biggest change from v2: there is **one LLM role
in the loop**. The standalone Challenger and the separate author-response pass are
gone. Every **specialist**, in one synchronized *sweep* over the previous ledger
snapshot, wears two hats:

* **as critic** — scans every claim it does NOT own and files a typed ``Challenge``
  on any it disagrees with (it hunts; it does not wait to be asked);
* **as owner** — sees the admitted objections on its OWN claims and makes exactly
  **one move per claim**: cite / concede / revise (a new version).

An **admissibility gate** runs before any owner spends a token:
``contradicting_evidence`` / ``missing_evidence`` objections must cite real
evidence ids; an ``inference_dispute`` must name a concrete ``inference_flaw`` (not
"I disagree"). Inadmissible objections are dropped — the manufactured-disagreement
killer.

"No chat room": the unit of exchange is a typed object bound to a ``claim_id`` +
evidence, never prose addressed to a colleague; one move per owner per claim per
sweep. Termination is a **fixed point** — a sweep that produces no admissible new
objection and no revision ("stability, not agreement"). The Crux & Sensitivity
controller (``crux.py``) decides, between sweeps, whether to run another sweep,
re-gather, or stop.

The whole step is deterministic *given the LLM outputs* (specialists run at
temperature 0): admissibility, verification, version-binding, and the confidence
rule are all code, so a frozen set of model outputs replays to an identical ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.core.evidence.confidence import (
    ConfidenceEvent,
    corroboration,
    missing_evidence as missing_event,
    refutation,
)
from server.core.evidence.models import (
    AuthorResponse,
    Challenge,
    ChallengeDraft,
    Response,
    Verification,
)
from server.core.evidence.store import EvidenceContext
from server.core.orchestration.verify import tier_and_trust, verify_challenge

DEFAULT_MAX_SWEEPS = 3

# Most-severe-first when choosing the single objection an owner answers per claim.
_SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1}


@dataclass
class ChallengeOutcome:
    challenge: Challenge
    verification: Verification
    response_action: str
    pre_confidence: float
    post_confidence: float
    new_status: str


@dataclass
class SweepLog:
    sweep_number: int
    filed: int = 0          # objections critics produced (pre-gate)
    admitted: int = 0       # passed admissibility + genuinely new (drives fixed point)
    dropped: int = 0        # inadmissible / manufactured / duplicate
    revisions: int = 0
    outcomes: list[ChallengeOutcome] = field(default_factory=list)


@dataclass
class ReviewResult:
    sweeps: list[SweepLog] = field(default_factory=list)
    stop_reason: str = ""


def _derive_events_and_status(
    verification: Verification,
    response: AuthorResponse,
    challenge: Challenge,
    pool,
    pre_conf: float,
) -> tuple[list[ConfidenceEvent], str]:
    """Map verifier findings + owner action onto confidence events + status.

    Source trust rides on every evidence-backed event: a high-tier but low-trust
    source moves confidence proportionally less than a trusted one.
    """
    if not verification.challenge_supported:
        return [], "_unchanged"  # rejected objection: the claim is unscathed

    ch_tier, ch_trust = tier_and_trust(pool, challenge.evidence_ids)
    events: list[ConfidenceEvent] = []

    if verification.contradiction:
        events.append(refutation(ch_tier, "verified contradicting evidence", source_trust=ch_trust))
    elif verification.missing_evidence:
        events.append(missing_event(ch_tier, "verified omitted evidence", source_trust=ch_trust))
    else:  # inference_dispute the verifier accepted as substantive (no new evidence)
        events.append(refutation("weak", "accepted inference dispute"))

    if response.action == "concede":
        events.append(refutation("strong", "owner conceded"))
        status = "refuted"
    elif response.action == "revise":
        status = "revised"  # the revision itself narrows/qualifies the claim
    else:  # cite (defend)
        if response.evidence_ids:
            tier, trust = tier_and_trust(pool, response.evidence_ids)
            events.append(corroboration(tier, "owner cited support", source_trust=trust))
        status = "challenged"
    return events, status


class AdversarialReview:
    """Single-role sweep over the claim ledger (critic + owner, one role)."""

    def __init__(self, specialists_by_label: dict, *, callbacks: list | None = None) -> None:
        self._specialists = specialists_by_label
        self._callbacks = callbacks
        # (claim, kind, evidence-set) signatures already filed — so a critic
        # re-raising the same objection next sweep does not recount as "new".
        self._seen: set[tuple] = set()

    # ---- one synchronized sweep -------------------------------------------

    def sweep(
        self,
        question: str,
        ctx: EvidenceContext,
        evidence_digest: str,
        valid_ids: set[str],
        sweep_number: int,
    ) -> SweepLog:
        log = SweepLog(sweep_number=sweep_number)
        snapshot = [c for c in ctx.ledger.claims() if c.status != "refuted"]
        valid_claim_ids = {c.claim_id for c in snapshot}

        # --- critic phase: each specialist objects to claims it does NOT own ---
        for label, agent in self._specialists.items():
            others = [c for c in snapshot if c.owner != label]
            if not others or not hasattr(agent, "review"):
                continue
            try:
                drafts = agent.review(question, others, evidence_digest, valid_ids,
                                      callbacks=self._callbacks)
            except Exception:  # noqa: BLE001 - a critic failure just yields no objections
                drafts = []
            for draft in drafts:
                log.filed += 1
                ch = self._admit(draft, label, valid_ids, valid_claim_ids)
                if ch is None:
                    log.dropped += 1
                    continue
                sig = (ch.target_claim, ch.kind, frozenset(ch.evidence_ids))
                if sig in self._seen:
                    log.dropped += 1
                    continue
                self._seen.add(sig)
                ctx.ledger.add_challenge(ch)   # version-bound; assigns id
                ch.status = "admitted"
                log.admitted += 1

        # --- owner phase: one move per OWN claim that has a pending objection ---
        for claim in snapshot:
            pending = ctx.ledger.pending_challenges_for(claim.claim_id)
            if not pending:
                continue
            primary = max(pending, key=lambda c: _SEVERITY_RANK.get(c.severity, 0))
            outcome = self._resolve(claim, primary, ctx, evidence_digest, valid_ids)
            log.outcomes.append(outcome)
            if outcome.response_action == "revise":
                log.revisions += 1

        return log

    # ---- run the inner loop (next-sweep / stable only; re-gather is the caller's) ---

    def run(
        self,
        question: str,
        ctx: EvidenceContext,
        evidence_digest: str,
        valid_ids: set[str],
        *,
        max_sweeps: int = DEFAULT_MAX_SWEEPS,
        crux_fn=None,
    ) -> ReviewResult:
        """Loop sweeps until a fixed point, a ``stable`` crux verdict, or the cap.

        ``crux_fn(ctx) -> "next_sweep" | "stable"`` lets a caller plug the Crux
        controller in for early-stop. The re-gather exit is handled by the
        pipeline (it must re-enter retrieval), not here.
        """
        result = ReviewResult()
        for n in range(1, max_sweeps + 1):
            log = self.sweep(question, ctx, evidence_digest, valid_ids, n)
            result.sweeps.append(log)
            if log.admitted == 0 and log.revisions == 0:
                result.stop_reason = "fixed point (no admissible new objection, no revision)"
                break
            if crux_fn is not None and crux_fn(ctx) == "stable":
                result.stop_reason = "crux: stable (nothing pivotal remains)"
                break
        else:
            result.stop_reason = "max sweeps reached"
        self.finalize(ctx)
        return result

    # ---- helpers -----------------------------------------------------------

    def _admit(
        self,
        draft: ChallengeDraft,
        author: str,
        valid_ids: set[str],
        valid_claim_ids: set[str],
    ) -> Challenge | None:
        """The admissibility gate: drop manufactured / uncited objections."""
        if draft.target_claim not in valid_claim_ids:
            return None
        cited = [e for e in draft.evidence_ids if e in valid_ids]
        if draft.kind in ("contradicting_evidence", "missing_evidence") and not cited:
            return None
        if draft.kind == "inference_dispute" and not (draft.inference_flaw or "").strip():
            return None
        return Challenge(
            challenge_id="",
            target_claim=draft.target_claim,
            challenger=author,
            kind=draft.kind,
            evidence_ids=cited,
            inference_flaw=draft.inference_flaw,
            severity=draft.severity,
            rationale=draft.rationale,
            status="candidate",
        )

    def _resolve(
        self,
        claim,
        challenge: Challenge,
        ctx: EvidenceContext,
        evidence_digest: str,
        valid_ids: set[str],
    ) -> ChallengeOutcome:
        ctx.ledger.set_status(claim.claim_id, "challenged")
        pre_conf = claim.confidence
        response = self._respond(claim, challenge, evidence_digest, valid_ids)

        resp_rec = Response(
            challenge_id=challenge.challenge_id,
            author=claim.owner,
            move=response.action,
            added_evidence_ids=list(response.evidence_ids),
            rationale=response.rationale,
        )
        revised = False
        if response.action == "revise" and response.new_assertion.strip():
            ctx.ledger.revise_claim(
                claim.claim_id,
                owner=claim.owner,
                new_assertion=response.new_assertion,
                evidence_ids=response.evidence_ids or claim.evidence_ids,
                rationale=response.rationale,
            )
            resp_rec.resulting_version = claim.version
            revised = True
        ctx.ledger.add_response(resp_rec)

        verification = verify_challenge(challenge, claim, ctx.pool)
        events, status = _derive_events_and_status(
            verification, response, challenge, ctx.pool, pre_conf
        )
        ctx.ledger.apply_events(claim.claim_id, events)
        post_conf = claim.confidence

        # Challenge terminal status. A revision has already version-bound this
        # objection to ``addressed`` (see ClaimLedger._rebind_versions); otherwise
        # it is verified (stuck) or rejected (failed) by the evidentiary check.
        if not revised:
            challenge.status = "verified" if verification.challenge_supported else "rejected"

        final_status = self._finalise_status(status, pre_conf, post_conf, claim.status)
        ctx.ledger.set_status(claim.claim_id, final_status)
        return ChallengeOutcome(
            challenge=challenge,
            verification=verification,
            response_action=response.action,
            pre_confidence=pre_conf,
            post_confidence=post_conf,
            new_status=final_status,
        )

    def _respond(self, claim, challenge, evidence_digest, valid_ids) -> AuthorResponse:
        owner = self._specialists.get(claim.owner)
        if owner is None or not hasattr(owner, "respond"):
            return AuthorResponse(action="cite", evidence_ids=list(claim.evidence_ids))
        try:
            return owner.respond(claim, challenge, evidence_digest, valid_ids,
                                 callbacks=self._callbacks)
        except Exception:  # noqa: BLE001 - a failed response defaults to a no-op defence
            return AuthorResponse(action="cite", evidence_ids=list(claim.evidence_ids))

    @staticmethod
    def _finalise_status(status: str, pre: float, post: float, current: str) -> str:
        if status == "_unchanged":
            return current if current != "challenged" else "asserted"
        if status == "challenged":
            return "corroborated" if post > pre else "challenged"
        return status

    @staticmethod
    def finalize(ctx: EvidenceContext) -> None:
        """After the loop, settle non-refuted claims to a terminal 'resolved'."""
        for claim in ctx.ledger.claims():
            if claim.status in ("asserted", "revised", "corroborated", "challenged"):
                ctx.ledger.set_status(claim.claim_id, "resolved")
