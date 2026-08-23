"""Per-run evidence context + registry — composes v3's two stores (L3).

v2 bundled evidence and claims into one store; v3 splits them into two services
with opposite write semantics:

* :class:`~server.core.evidence.evidence_pool.EvidencePool` — immutable,
  append-only, content-addressed; written only by retrieval/quant.
* :class:`~server.core.evidence.claim_ledger.ClaimLedger` — mutable, owner-only,
  versioned; the three record types (Claim / Challenge / Response).

Both live inside an :class:`EvidenceContext`, one per ``run_id``, registered in a
module-level map that mirrors how ``memory.shared_memory`` is shared process-wide.
The pipeline creates a context at the start of a run and tears it down at the end;
``run_manager`` snapshots it into the persisted transcript.

This module re-exports the store classes so existing imports
(``from server.core.evidence.store import EvidenceContext, args_hash, ...``)
keep working after the split.
"""

from __future__ import annotations

import threading

from server.core.evidence.claim_ledger import ClaimLedger, OwnershipError
from server.core.evidence.evidence_pool import EvidencePool, EvidenceStore, args_hash

__all__ = [
    "EvidencePool",
    "EvidenceStore",
    "ClaimLedger",
    "OwnershipError",
    "args_hash",
    "EvidenceContext",
    "create_context",
    "get_context",
    "clear_context",
]


class EvidenceContext:
    """Per-run bundle of the Evidence Pool and the Claim Ledger."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.pool = EvidencePool()
        self.ledger = ClaimLedger()

    @property
    def store(self) -> EvidencePool:
        """Back-compat alias: v2 called the Evidence Pool the 'store'."""
        return self.pool

    def snapshot(self) -> dict:
        """Serialise the whole L3 state for persistence in the transcript."""
        return {
            "evidence": [r.model_dump() for r in self.pool.all()],
            "claims": [c.model_dump() for c in self.ledger.claims()],
            "challenges": [c.model_dump() for c in self.ledger.challenges()],
            "responses": [r.model_dump() for r in self.ledger.responses()],
        }


# --- per-run registry (mirrors memory.shared_memory) ------------------------

_contexts: dict[str, EvidenceContext] = {}
_registry_lock = threading.RLock()


def create_context(run_id: str) -> EvidenceContext:
    """Open a fresh L3 context for a run (replacing any stale one)."""
    with _registry_lock:
        ctx = EvidenceContext(run_id)
        _contexts[run_id] = ctx
        return ctx


def get_context(run_id: str) -> EvidenceContext | None:
    with _registry_lock:
        return _contexts.get(run_id)


def clear_context(run_id: str) -> None:
    with _registry_lock:
        _contexts.pop(run_id, None)
