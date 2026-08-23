"""Gap detection over MCP tool payloads.

A researcher writes every MCP result as an ``EvidenceRecord``, but not every
result is *usable*: a tool can error, or it can succeed yet find nothing. These
two predicates classify a payload so the Sufficiency gate can tell a real gap
from real evidence (and a "searched, provably absent" negative from a failure
worth retrying). Moved here from the old retrieval layer — they are purely about
payload shape, independent of where the payload came from.
"""

from __future__ import annotations


def is_empty_payload(payload: object) -> bool:
    """Did a tool call return nothing usable?

    Empty/error records exist for audit but do not count toward coverage in the
    Sufficiency gate.
    """
    if payload is None:
        return True
    if isinstance(payload, dict):
        if payload.get("error"):
            return True
        # Tool-specific emptiness signals.
        if "matched_players" in payload and not payload.get("matched_players"):
            return True
        if "results" in payload and not payload.get("results"):
            return True
        if "fixtures" in payload and not payload.get("fixtures"):
            return True
        if payload.get("matches_considered") == 0:
            return True
    if isinstance(payload, (list, str)) and len(payload) == 0:
        return True
    return False


def is_negative_result(payload: object) -> bool:
    """Did a tool SUCCEED but find nothing? ("searched, provably absent").

    Distinct from an error/failure: a clean empty result *resolves* a sufficiency
    slot by absence (a stated limitation) instead of looping retrieval forever. An
    error payload (``{"error": ...}``) is NOT a negative result — it is a failure
    to look, which should be retried.
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return False
    if "matched_players" in payload and not payload.get("matched_players"):
        return True
    if "results" in payload and not payload.get("results"):
        return True
    if "fixtures" in payload and not payload.get("fixtures"):
        return True
    if payload.get("matches_considered") == 0:
        return True
    return False
