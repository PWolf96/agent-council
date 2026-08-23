"""Deterministic confidence service (L2) — the log-odds update rule."""

from server.core.evidence.confidence import (
    P_MAX,
    P_MIN,
    corroboration,
    missing_evidence,
    refutation,
    update_confidence,
)


def test_no_events_is_identity():
    assert update_confidence(0.5, []) == 0.5


def test_refutation_lowers_corroboration_raises():
    base = 0.6
    assert update_confidence(base, [refutation("strong")]) < base
    assert update_confidence(base, [corroboration("strong")]) > base


def test_determinism_same_inputs_same_output():
    events = [refutation("strong"), corroboration("moderate")]
    a = update_confidence(0.7, events)
    b = update_confidence(0.7, events)
    assert a == b


def test_clamped_to_bounds():
    # Pile on corroboration -> never reaches certainty.
    p = 0.9
    for _ in range(20):
        p = update_confidence(p, [corroboration("authoritative")])
    assert p <= P_MAX
    # Pile on refutation -> never reaches impossibility.
    p = 0.1
    for _ in range(20):
        p = update_confidence(p, [refutation("authoritative")])
    assert p >= P_MIN


def test_stronger_tier_moves_more():
    base = 0.5
    weak_drop = base - update_confidence(base, [refutation("weak")])
    strong_drop = base - update_confidence(base, [refutation("authoritative")])
    assert strong_drop > weak_drop > 0


def test_worked_example_shape():
    # Design's worked example: 0.82 -> (availability challenge) ~0.6 -> (fitness) up.
    p0 = 0.82
    p1 = update_confidence(p0, [refutation("strong")])
    p2 = update_confidence(p1, [corroboration("authoritative")])
    assert p1 < p0
    assert p2 > p1
    assert 0.55 <= p1 <= 0.68  # roughly the design's 0.61


def test_missing_evidence_is_negative():
    base = 0.6
    assert update_confidence(base, [missing_evidence("moderate")]) < base
