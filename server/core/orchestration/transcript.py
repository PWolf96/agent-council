"""Reduce a workflow's event stream into a persisted run payload.

The pipeline emits typed events (roster, plan, retrieval, sufficiency, claim,
sweep, crux, contradictions, decision, evaluation, evidence_snapshot). This folds
them into the record stored on disk.

Two faces of the same payload:

* A **timeline view** — ``debateHistory`` / ``judgeHistory`` / ``finalVerdict`` —
  rendered from the claims, passes, and decision. The follow-up chat and the web
  UI read these, so they are kept stable.
* The **full record** — ``plan``, ``retrieval``, ``sufficiency``, ``claims``,
  ``passes``, ``contradictions``, ``decision``, and the evidence-store snapshot —
  carried at the top level of ``result`` for audit and the API.
"""

from __future__ import annotations


def _evidence_refs(evidence_ids: list[str]) -> list[dict]:
    """Render a claim's citations as the UI's ``tool_calls`` shape (the evidence
    behind the argument)."""
    if not evidence_ids:
        return []
    return [{"tool": "cite", "args": {"evidence_ids": evidence_ids}, "result": ""}]


def _claim_owner(claims: list[dict], claim_id: str | None) -> str:
    for c in claims:
        if c.get("claim_id") == claim_id:
            return c.get("owner", "")
    return ""


def build_run_payload(events: list[dict]) -> dict:
    roster: dict = {}
    plan: dict = {}
    sufficiency: dict = {}
    retrieval: dict = {}
    claims: list[dict] = []
    sweeps: list[dict] = []
    cruxes: list[dict] = []
    evaluation: dict = {}
    contradictions: list[dict] = []
    decision: dict = {}
    snapshot: dict = {}
    routing: dict | None = None
    final_verdict = ""

    for ev in events:
        etype = ev.get("event")
        data = ev.get("data", {})
        if etype == "routing":
            routing = data
        elif etype == "roster":
            roster = data
        elif etype == "plan":
            plan = data
        elif etype == "retrieval":
            retrieval = data
        elif etype == "sufficiency":
            sufficiency = data
        elif etype == "claim":
            claims.append(data)
        elif etype == "sweep":
            sweeps.append(data)
        elif etype == "crux":
            cruxes.append(data)
        elif etype == "evaluation":
            evaluation = data
        elif etype == "contradictions":
            contradictions = data
        elif etype == "decision":
            decision = data
        elif etype == "verdict":
            final_verdict = data.get("text", "")
        elif etype == "evidence_snapshot":
            snapshot = data

    # --- timeline view (consumed by the UI + follow-up chat) ----------------
    # Round 1: each initial claim as an "argument" by its owner.
    debate_history: list[dict] = []
    for c in claims:
        debate_history.append({
            "round": 1,
            "agent": c.get("owner", ""),
            "argument": (
                f"[{c.get('claim_id')}] (confidence {c.get('confidence')}) "
                f"{c.get('assertion', '')}"
            ),
            "tool_calls": _evidence_refs(c.get("evidence_ids", [])),
        })
    # Later rounds: each adversarial sweep, as the owner's revised stance.
    for slog in sweeps:
        rnd = 1 + slog.get("sweep", 0)
        for o in slog.get("outcomes", []):
            debate_history.append({
                "round": rnd,
                "agent": _claim_owner(claims, o.get("target_claim")),
                "argument": (
                    f"Challenge {o.get('challenge_id')} ({o.get('kind')}, {o.get('severity')}) "
                    f"by {o.get('challenger')} on {o.get('target_claim')} → owner "
                    f"{o.get('response')}; confidence {o.get('pre_confidence')}→"
                    f"{o.get('post_confidence')} [{o.get('status')}]"
                ),
                "tool_calls": [],
            })
    max_round = max((e["round"] for e in debate_history), default=0)

    dissent = decision.get("unresolved_dissent", [])
    reasoning = (
        f"Decision confidence {decision.get('confidence')}. "
        f"Supporting claims: {decision.get('supporting_claims', [])}. "
        + (f"Surviving dissent ({len(dissent)}): "
           + "; ".join(d.get("summary", "") for d in dissent)
           if dissent else "No surviving dissent.")
    )
    judge_history = [{
        "round": max_round,
        "reasoning": reasoning,
        "scoreBreakdown": f"decision_confidence = {decision.get('confidence')}",
        "scores": None,
        "verdict": final_verdict,
    }] if decision else []

    return {
        "result": {
            "rounds": max_round,
            "debateHistory": debate_history,
            "roundSummaries": [],
            "judgeHistory": judge_history,
            "decided": bool(decision),
            "finalVerdict": final_verdict,
            # full evidence-grounded record (top-level, for audit + API)
            "roster": roster,
            "plan": plan,
            "retrieval": retrieval,
            "sufficiency": sufficiency,
            "claims": claims,
            "sweeps": sweeps,
            "cruxes": cruxes,
            "evaluation": evaluation,
            "contradictions": contradictions,
            "decision": decision,
            "evidence": snapshot.get("evidence", []),
            "ledgerClaims": snapshot.get("claims", []),
            "challenges": snapshot.get("challenges", []),
            "responses": snapshot.get("responses", []),
        },
        "routing": routing,
        "insufficientAgents": None,
        "decided": bool(decision),
    }
