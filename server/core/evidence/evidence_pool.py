"""Evidence Pool (L3 · STORE 1) — immutable, append-only, content-addressed.

The first of v3's two stores. Written **only** by retrieval (the researcher team,
L2) and the Quant Forecaster (L2q); **read-only** for everything downstream. Every
tool/quant result lands here exactly once, keyed by a normalised
``(tool, args, as_of)`` hash — re-requesting the same fact returns the existing
record, which *is* the dedup cache at the store level and what makes parallel
retrieval safe.

The one-way dependency is structural: claims (in the Claim Ledger) cite evidence
ids; evidence never references claims. Nothing here calls an LLM or a network.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from server.core.evidence.models import EvidenceRecord, StrengthTier


def _normalise_args(args: dict | None) -> dict:
    """Stable view of tool args for hashing (order-independent, JSON-safe)."""
    return json.loads(json.dumps(args or {}, sort_keys=True, default=str))


def args_hash(tool: str, args: dict | None, as_of: str | None) -> str:
    """Content address for a retrieval: same inputs -> same key -> one record."""
    blob = json.dumps(
        {"tool": tool, "args": _normalise_args(args), "as_of": as_of},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class EvidencePool:
    """Append-only, deduplicated store of immutable evidence records."""

    def __init__(self) -> None:
        self._by_hash: dict[str, EvidenceRecord] = {}
        self._by_id: dict[str, EvidenceRecord] = {}
        self._seq = 0
        self._lock = threading.RLock()

    def _next_id(self) -> str:
        self._seq += 1
        return f"E{self._seq:02d}"

    def add(
        self,
        *,
        source_tool: str,
        args: dict | None,
        payload: Any,
        strength_tier: StrengthTier = "moderate",
        source_trust: float = 1.0,
        provenance: str = "",
        as_of: str | None = None,
        covers: list[str] | None = None,
        is_empty: bool = False,
        is_negative_result: bool = False,
    ) -> EvidenceRecord:
        """Store a fact, or return the existing record for identical inputs.

        Dedup is by ``args_hash``. When a fact already exists we still merge any
        new ``covers`` labels onto it (the same query can satisfy more than one
        plan item) so coverage stays accurate.
        """
        key = args_hash(source_tool, args, as_of)
        with self._lock:
            existing = self._by_hash.get(key)
            if existing is not None:
                if covers:
                    merged = list(dict.fromkeys([*existing.covers, *covers]))
                    existing.covers = merged
                return existing

            record = EvidenceRecord(
                id=self._next_id(),
                source_tool=source_tool,
                args_hash=key,
                args=_normalise_args(args),
                payload=payload,
                retrieved_at=time.time(),
                strength_tier=strength_tier,
                source_trust=max(0.0, min(1.0, source_trust)),
                provenance=provenance,
                as_of=as_of,
                covers=list(covers or []),
                is_empty=is_empty,
                is_negative_result=is_negative_result,
            )
            self._by_hash[key] = record
            self._by_id[record.id] = record
            return record

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        return self._by_id.get(evidence_id)

    def exists(self, evidence_id: str) -> bool:
        return evidence_id in self._by_id

    def by_ids(self, ids: list[str]) -> list[EvidenceRecord]:
        return [self._by_id[i] for i in ids if i in self._by_id]

    def all(self) -> list[EvidenceRecord]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)


# Back-compat alias: v2 called this the "Evidence Store".
EvidenceStore = EvidencePool
