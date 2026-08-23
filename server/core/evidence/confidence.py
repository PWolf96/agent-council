"""Deterministic confidence service (L2).

Confidence numbers in v2 are **never** produced by a language model. A claim's
confidence is recomputed by a transparent log-odds rule on every *verified*
deliberation event, so identical evidence events always yield identical
confidence — the property that makes Brier/log-loss calibration possible.

The rule (from the design doc)::

    L  = ln(p / (1 - p))                         # current confidence -> log-odds
    L' = L + Σ (sign · weight(strength_tier))    # one term per verified event
    p' = clamp(sigmoid(L'), 0.05, 0.95)          # bounded, auditable

Each verified event contributes a fixed-sign, tier-weighted term:

* a corroborating evidence record               -> ``+``
* a verified refuting / contradicting challenge -> ``−``
* a verified missing-evidence flag              -> ``−``

The owner's revision supplies new *assertion text* (LLM, temp 0); the confidence
*value* is this function's job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from server.core.evidence.models import StrengthTier

# How much each strength tier moves the log-odds. Tuned so a single
# authoritative refutation roughly halves an even claim's odds while a weak
# corroboration nudges it. These weights are the dial calibration tunes against
# outcomes (see the design doc's "confidence calibration" focus area).
TIER_WEIGHTS: dict[StrengthTier, float] = {
    "weak": 0.35,
    "moderate": 0.70,
    "strong": 1.10,
    "authoritative": 1.60,
}

# Confidence is clamped to this open interval: a claim is never certain and never
# impossible, which keeps later log-odds updates able to move it.
P_MIN = 0.05
P_MAX = 0.95


@dataclass(frozen=True)
class ConfidenceEvent:
    """One verified event feeding the log-odds update.

    ``sign`` is +1 for support/corroboration, -1 for refutation/missing evidence.
    ``tier`` selects the magnitude from :data:`TIER_WEIGHTS`. ``source_trust``
    (0–1) scales that magnitude so a noisy source (e.g. fan sentiment) moves
    confidence structurally less than structured data at the same tier.
    """

    sign: int
    tier: StrengthTier
    reason: str = ""
    source_trust: float = 1.0

    @property
    def delta(self) -> float:
        trust = max(0.0, min(1.0, self.source_trust))
        return self.sign * trust * TIER_WEIGHTS.get(self.tier, TIER_WEIGHTS["weak"])


def logit(p: float) -> float:
    p = clamp(p, P_MIN, P_MAX)
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    # Numerically stable for large |x|.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def clamp(value: float, lo: float = P_MIN, hi: float = P_MAX) -> float:
    return max(lo, min(hi, value))


def update_confidence(current: float, events: list[ConfidenceEvent]) -> float:
    """Apply verified ``events`` to ``current`` confidence via the log-odds rule.

    Pure and deterministic: same ``current`` + same ``events`` -> same result.
    Returns a probability clamped to ``[P_MIN, P_MAX]`` and rounded so persisted
    histories stay stable across runs.
    """
    if not events:
        return round(clamp(current), 4)
    total = logit(current) + sum(e.delta for e in events)
    return round(clamp(sigmoid(total)), 4)


def corroboration(tier: StrengthTier, reason: str = "", source_trust: float = 1.0) -> ConfidenceEvent:
    return ConfidenceEvent(
        sign=+1, tier=tier, reason=reason or "corroborating evidence", source_trust=source_trust
    )


def refutation(tier: StrengthTier, reason: str = "", source_trust: float = 1.0) -> ConfidenceEvent:
    return ConfidenceEvent(
        sign=-1, tier=tier, reason=reason or "verified refutation", source_trust=source_trust
    )


def missing_evidence(
    tier: StrengthTier = "moderate", reason: str = "", source_trust: float = 1.0
) -> ConfidenceEvent:
    return ConfidenceEvent(
        sign=-1, tier=tier, reason=reason or "decisive evidence missing", source_trust=source_trust
    )
