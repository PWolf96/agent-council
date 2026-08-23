"""Specialist Analysis (L3) — turn shared evidence into owned, cited claims.

Each assigned specialist reads the *same* digest of the Evidence Store and writes
typed claims within its domain. This is the v2 replacement for v1's opening
round of free-form prose: the output is structured ``Claim`` records, each owned
by its author and citing evidence ids, persisted in the Claim Ledger.

Two invariants enforced here:

* **Grounding.** A claim with no valid citation is dropped (the claim-writer
  already filters these; the ledger would reject them anyway).
* **De-duplication.** Two specialists asserting the same thing in the same
  dimension collapse to one claim (design L3: de-dup on
  ``(dimension, normalised assertion)``), so the loop doesn't waste budget
  re-litigating duplicates.
"""

from __future__ import annotations

import json
import re

from server.core.evidence.models import Claim, ResearchPlan
from server.core.evidence.store import EvidenceContext

# Per-record payload budget in the digest — enough for a stat block or a quant
# summary, trimmed so a chatty tool can't blow up the specialist's context.
_MAX_RECORD_CHARS = 1000
# A multi-row payload (a table of N players, fixtures, …) is rendered row by row.
# These caps bound it generously but still trim a runaway result — and, crucially,
# they trim at a *row boundary* so a row is never cut in half (the old flat char
# cap left only the first row of a multi-row result).
_MAX_ROWS = 50
_MAX_LIST_CHARS = 6000


def _render_rows(rows: list) -> str:
    """Render a list payload as whole rows, trimming at a row boundary."""
    kept: list[str] = []
    total = 0
    for row in rows[:_MAX_ROWS]:
        line = json.dumps(row, default=str)
        if kept and total + len(line) > _MAX_LIST_CHARS:
            break
        kept.append(line)
        total += len(line) + 1
    dropped = len(rows) - len(kept)
    text = "\n".join(kept)
    if dropped > 0:
        text += f"\n… (+{dropped} more rows)"
    return text


def _render_payload(payload: object) -> str:
    """Compact, grounded view of one evidence payload for a specialist."""
    if isinstance(payload, list):
        # Multi-row result (e.g. a table of players) — keep every row whole.
        return _render_rows(payload)
    if isinstance(payload, dict) and payload.get("summary"):
        # Quant records lead with a one-line calibrated summary; keep it whole.
        head = str(payload["summary"])
        rest = {k: v for k, v in payload.items() if k != "summary"}
        body = json.dumps(rest, default=str)
        text = f"{head}\n{body}"
    else:
        text = json.dumps(payload, default=str)
    if len(text) > _MAX_RECORD_CHARS:
        text = text[:_MAX_RECORD_CHARS] + "… (truncated)"
    return text


def render_evidence_digest(ctx: EvidenceContext) -> tuple[str, set[str]]:
    """Render every non-empty evidence record into a citable digest.

    Returns ``(digest_text, valid_ids)``. Only non-empty records are offered, so
    specialists can only cite facts that actually exist.
    """
    blocks: list[str] = []
    valid_ids: set[str] = set()
    for record in ctx.store.all():
        if record.is_empty:
            continue
        valid_ids.add(record.id)
        label = ", ".join(record.covers) or record.source_tool
        blocks.append(
            f"[{record.id}] ({record.provenance}/{record.strength_tier}) {label}\n"
            f"{_render_payload(record.payload)}"
        )
    return "\n\n".join(blocks), valid_ids


def _normalise(assertion: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", assertion.lower()))


def run_specialists(
    plan: ResearchPlan,
    ctx: EvidenceContext,
    agents_by_key: dict,
    *,
    callbacks: list | None = None,
) -> list[Claim]:
    """Run each assigned specialist over the shared evidence; write claims."""
    digest, valid_ids = render_evidence_digest(ctx)
    if not valid_ids:
        return []

    written: list[Claim] = []
    seen: set[tuple[str, str]] = set()
    for key in plan.assigned_specialists:
        agent = agents_by_key.get(key)
        if agent is None:
            continue
        try:
            drafts = agent.write_claims(plan.question, digest, valid_ids, callbacks=callbacks)
        except Exception:  # noqa: BLE001 - a specialist failing must not abort analysis
            continue
        for draft in drafts:
            if not draft.evidence_ids:
                continue
            fingerprint = (agent.dimension, _normalise(draft.assertion))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            claim = ctx.ledger.add_claim(
                owner=agent.label,
                dimension=agent.dimension,
                assertion=draft.assertion,
                evidence_ids=draft.evidence_ids,
                confidence=draft.initial_confidence,
                rationale=draft.rationale,
            )
            written.append(claim)
    return written
