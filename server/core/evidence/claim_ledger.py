"""Claim Ledger (L3 · STORE 2) — mutable, owner-only, versioned.

The second of v3's two stores, with the opposite write semantics to the Evidence
Pool: claims are *mutated*, but only by their ``owner`` (everyone else may only
challenge). It holds **three separate record types** — ``Claim``, ``Challenge``,
``Response`` — not one record with critique fields. "All critiques of C12" is a
reverse lookup over challenges, not a list stored on the claim.

**Version-binding.** A ``Challenge`` is bound to the ``target_version`` it was
filed against. When the owner revises a claim to a new version, challenges filed
against the prior version are marked ``addressed``/``stale`` and do not carry over
to attack text that no longer exists.

Confidence is updated exclusively through the deterministic rule in
``confidence.py``; an LLM never writes a confidence number here.
"""

from __future__ import annotations

import threading

from server.core.evidence.confidence import ConfidenceEvent, update_confidence
from server.core.evidence.models import (
    Challenge,
    Claim,
    ClaimRevision,
    ClaimStatus,
    Response,
)


class OwnershipError(RuntimeError):
    """Raised when an agent tries to mutate a claim it does not own."""


class ClaimLedger:
    """Owned, versioned claims + their challenges + owner responses."""

    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}
        self._challenges: dict[str, Challenge] = {}
        self._responses: dict[str, Response] = {}
        self._claim_seq = 0
        self._challenge_seq = 0
        self._response_seq = 0
        self._lock = threading.RLock()

    # ---- claims ------------------------------------------------------------

    def _next_claim_id(self) -> str:
        self._claim_seq += 1
        return f"C{self._claim_seq:02d}"

    def add_claim(
        self,
        *,
        owner: str,
        dimension: str,
        assertion: str,
        evidence_ids: list[str],
        confidence: float,
        confidence_kind: str = "judgmental",
        rationale: str = "",
        depends_on: list[str] | None = None,
    ) -> Claim:
        with self._lock:
            claim = Claim(
                claim_id=self._next_claim_id(),
                owner=owner,
                dimension=dimension,
                version=1,
                assertion=assertion,
                evidence_ids=list(evidence_ids),
                confidence=round(confidence, 4),
                confidence_history=[round(confidence, 4)],
                confidence_kind=confidence_kind,  # type: ignore[arg-type]
                status="asserted",
                rationale=rationale,
                depends_on=list(depends_on or []),
            )
            self._claims[claim.claim_id] = claim
            return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        return self._claims.get(claim_id)

    def claims(self) -> list[Claim]:
        return list(self._claims.values())

    def _require(self, claim_id: str) -> Claim:
        claim = self._claims.get(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim {claim_id!r}")
        return claim

    def revise_claim(
        self,
        claim_id: str,
        *,
        owner: str,
        new_assertion: str,
        evidence_ids: list[str] | None = None,
        rationale: str = "",
    ) -> Claim:
        """Owner-only revision: archive the current version, bump to a new one.

        Confidence is *not* set here — it is recomputed by ``apply_events`` from
        the verified deliberation events. As a side effect, version-binding marks
        every still-open challenge filed against the *prior* version as
        ``addressed`` (it attacked text that no longer exists).
        """
        with self._lock:
            claim = self._require(claim_id)
            if claim.owner != owner:
                raise OwnershipError(
                    f"{owner!r} may not revise claim {claim_id} owned by {claim.owner!r}"
                )
            prior_version = claim.version
            claim.revisions.append(
                ClaimRevision(
                    version=claim.version,
                    assertion=claim.assertion,
                    confidence=claim.confidence,
                )
            )
            claim.version += 1
            claim.assertion = new_assertion
            if evidence_ids is not None:
                claim.evidence_ids = list(evidence_ids)
            if rationale:
                claim.rationale = rationale
            claim.status = "revised"
            self._rebind_versions(claim_id, prior_version)
            return claim

    def _rebind_versions(self, claim_id: str, prior_version: int) -> None:
        """Mark challenges filed against ``prior_version`` as addressed/stale."""
        for ch in self._challenges.values():
            if ch.target_claim != claim_id:
                continue
            if ch.target_version <= prior_version and ch.status in (
                "candidate", "admitted", "open", "verified",
            ):
                # An admitted/verified objection the revision answers is "addressed";
                # an un-acted candidate against old text is "stale".
                ch.status = "addressed" if ch.status in ("admitted", "verified") else "stale"

    def set_status(self, claim_id: str, status: ClaimStatus) -> Claim:
        with self._lock:
            claim = self._require(claim_id)
            claim.status = status
            return claim

    def apply_events(self, claim_id: str, events: list[ConfidenceEvent]) -> Claim:
        """Recompute a claim's confidence via the deterministic log-odds rule.

        Appends the new value to ``confidence_history`` so the full trail is
        auditable. The status is *not* changed here; callers set it based on the
        verifier's findings (corroborated / refuted / resolved).
        """
        with self._lock:
            claim = self._require(claim_id)
            if not events:
                return claim
            new_conf = update_confidence(claim.confidence, events)
            claim.confidence = new_conf
            claim.confidence_history.append(new_conf)
            return claim

    # ---- challenges --------------------------------------------------------

    def _next_challenge_id(self) -> str:
        self._challenge_seq += 1
        return f"X{self._challenge_seq:02d}"

    def add_challenge(self, challenge: Challenge) -> Challenge:
        """Register a challenge, version-bound to its target claim's version.

        No list is stored on the claim — challenges point *in*; use
        :meth:`challenges_for` for the reverse lookup.
        """
        with self._lock:
            if not challenge.challenge_id:
                challenge.challenge_id = self._next_challenge_id()
            claim = self._claims.get(challenge.target_claim)
            if claim is not None:
                challenge.target_version = claim.version
            self._challenges[challenge.challenge_id] = challenge
            return challenge

    def get_challenge(self, challenge_id: str) -> Challenge | None:
        return self._challenges.get(challenge_id)

    def challenges(self) -> list[Challenge]:
        return list(self._challenges.values())

    def challenges_for(self, claim_id: str) -> list[Challenge]:
        """Reverse lookup: all critiques of ``claim_id`` (any version)."""
        return [c for c in self._challenges.values() if c.target_claim == claim_id]

    def pending_challenges_for(self, claim_id: str) -> list[Challenge]:
        """Admitted-but-unanswered challenges on the current version (need a move)."""
        claim = self._claims.get(claim_id)
        version = claim.version if claim else 0
        return [
            c for c in self._challenges.values()
            if c.target_claim == claim_id
            and c.target_version == version
            and c.status == "admitted"
        ]

    def open_challenges_for(self, claim_id: str) -> list[Challenge]:
        """Live disagreement on the current version: admitted (unanswered) or
        verified (answered but stuck). Used by the Crux controller to tell whether
        a pivotal claim is still contested."""
        claim = self._claims.get(claim_id)
        version = claim.version if claim else 0
        return [
            c for c in self._challenges.values()
            if c.target_claim == claim_id
            and c.target_version == version
            and c.status in ("admitted", "verified")
        ]

    # ---- responses ---------------------------------------------------------

    def _next_response_id(self) -> str:
        self._response_seq += 1
        return f"R{self._response_seq:02d}"

    def add_response(self, response: Response) -> Response:
        """Register an owner's response record (points at a Challenge)."""
        with self._lock:
            if not response.response_id:
                response.response_id = self._next_response_id()
            self._responses[response.response_id] = response
            return response

    def responses(self) -> list[Response]:
        return list(self._responses.values())

    def responses_for(self, challenge_id: str) -> list[Response]:
        return [r for r in self._responses.values() if r.challenge_id == challenge_id]
