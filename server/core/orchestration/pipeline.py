"""Pipeline orchestrator — composes L0→L6 into one streamed run.

Rather than a graph framework, the layers are an explicit, ordered driver: the
layer boundaries are real module seams (orchestrator / planner / researchers /
sufficiency / analysis / deliberation / crux / contradiction / decision /
evaluator), the bounded loops are explicit ``for``/``while`` loops with hard caps,
and the shared state is the per-run :class:`EvidenceContext` — the **two stores**
(Evidence Pool + Claim Ledger) every layer reads from and writes to.

Two control owners, as in the design:

* the **Crux** (L4c) is the *inner-loop* controller — "is deliberation done?" —
  with three exits (next sweep / re-gather / stable);
* the **Orchestrator** (L0) is the *outer-lifecycle* controller — "is the whole
  run acceptable?" — owning roster selection, the global budget, the single
  planner re-entry counter (shared by re-gather and the Evaluator's completeness
  retry), and the failure-routed retry.

Determinism is scoped to the **math spine**: the only low-variance steps are the
temperature-0 LLM calls (planner sketch, specialists as authors/critics/owners,
synthesizer); everything between them — coverage, confidence, sensitivity,
contradiction ranking, aggregation, the rubric — is code.

Data flow at a glance::

    question
      └─► Orchestrator (L0) ──► RosterSelection
            └─► Planner (L1, within roster) ──► ResearchPlan
                  └─► Researchers (L2) + Quant (L2q) ──► Evidence Pool   [STORE 1]
                        └─► Sufficiency (L2a) ┤ critical gap → re-gather (shared counter)
                              └─► Specialists (L4) ──► Claim Ledger      [STORE 2]
                                    └─► Adversarial sweep ⇄ Crux (L4c): sweep | re-gather | stable
                                          └─► Contradiction (L5a) ──► Decision (L5)
                                                └─► Evaluator (L6) ┤ fail → retry | pass → answer
"""

from __future__ import annotations

from typing import Iterator

from server.core.agents.general.planner import build_research_plan, heuristic_sketch
from server.core.agents.models import DEFAULT_MODEL
from server.core.agents.researchers.registry import get_researchers
from server.core.agents.teams import get_team
from server.core.evidence.store import clear_context, create_context
from server.core.orchestration import crux as crux_ctl
from server.core.orchestration.analysis import render_evidence_digest, run_specialists
from server.core.orchestration.contradiction import resolve_contradictions
from server.core.orchestration.decision import decide
from server.core.orchestration.deliberation import AdversarialReview
from server.core.orchestration.evaluator import evaluate, retry_target
from server.core.orchestration.observability import deliberation_trace
from server.core.orchestration.orchestrator import Budget, RunState, select_roster
from server.core.orchestration.researchers import dispatch_research
from server.core.orchestration.sufficiency import review_sufficiency

DEFAULT_MAX_SWEEPS = 3
DEFAULT_MAX_REENTRIES = 2
DEFAULT_MAX_RETRIES = 2


def stream_deliberation(
    topic: str,
    team_id: str,
    *,
    session_id: str = "default",
    smart_routing: bool = False,
    agent_keys: list[str] | None = None,
    default_model: str = DEFAULT_MODEL,
    agent_models: dict[str, str] | None = None,
    max_passes: int = DEFAULT_MAX_SWEEPS,
    per_pass_budget: int = 4,  # accepted for config compat; sweeps are gated by admissibility+dedup
    use_llm: bool = True,
    max_reentries: int = DEFAULT_MAX_REENTRIES,
    max_retries: int = DEFAULT_MAX_RETRIES,
    token_budget: float = 0.0,
) -> Iterator[dict]:
    """Run the v3 deliberation pipeline for one question, yielding events as it goes."""
    agent_models = agent_models or {}
    team = get_team(team_id)
    team_agents = list(team.agents)

    # Honour an explicit agent subset (smart routing lets the planner choose).
    candidate_agents = team_agents
    if not smart_routing and agent_keys:
        wanted = set(agent_keys)
        subset = [a for a in team_agents if a.key in wanted]
        if subset:
            candidate_agents = subset

    with deliberation_trace(
        name="deliberation", session_id=session_id, tags=[f"team:{team_id}"], input=topic
    ) as trace:
        cb = trace.callbacks

        # ---- L0: Orchestrator — roster selection + lifecycle ----------------
        qtype = heuristic_sketch(topic, candidate_agents).question_type
        roster = select_roster(topic, candidate_agents, question_type=qtype)
        roster_agents = [a for a in candidate_agents if a.key in set(roster.admitted_specialists)]
        if not roster_agents:
            roster_agents = candidate_agents
        state = RunState(
            run_id=session_id,
            max_sweeps=max_passes,
            max_reentries=max_reentries,
            max_retries=max_retries,
            budget=Budget(ceiling=token_budget),
            trace_id=getattr(trace, "trace_id", "") or "",
        )
        state.advance("selecting")
        yield {"event": "roster", "data": roster.model_dump()}

        # ---- L1: Plan, within the selected roster ---------------------------
        state.advance("planning")
        plan = build_research_plan(
            topic, roster_agents,
            researchers=roster.admitted_researchers,
            use_llm=use_llm, callbacks=cb,
        )
        assigned = [a for a in roster_agents if a.key in set(plan.assigned_specialists)]
        if not assigned:
            assigned = roster_agents
            plan.assigned_specialists = [a.key for a in assigned]
        yield {"event": "routing", "data": {
            "enabled": smart_routing,
            "selected": [{"key": a.key, "label": a.label} for a in assigned],
            "skipped": [{"key": a.key, "label": a.label}
                        for a in team_agents if a not in assigned],
            "reasoning": plan.reasoning,
        }}
        yield {"event": "plan", "data": plan.model_dump()}

        # ---- L2 / L2q: Researcher team + Quant (deterministic, cached) ------
        state.advance("retrieving")
        ctx = create_context(session_id)
        researchers = get_researchers()
        research = dispatch_research(
            plan, ctx, researchers=researchers, model_name=default_model, callbacks=cb
        )
        yield {"event": "retrieval", "data": {
            "requests_run": research.requests_run,
            "records": len(ctx.pool),
            "failures": research.failures,
            "negatives": research.negatives,
            "workers": research.workers,
            "breadth": research.breadth,
            "cache_stats": research.cache_stats,
        }}

        # ---- L2a: Sufficiency gate (informational) --------------------------
        # The planner declares no per-need labels (each researcher decides what to
        # fetch), so the gate reports on what landed but no longer drives a
        # targeted re-gather.
        state.advance("checking")
        report = review_sufficiency(plan, ctx)
        yield {"event": "sufficiency",
               "data": report.model_dump() | {"reentry_cycle": state.reentry_cycle}}

        digest, valid_ids = render_evidence_digest(ctx)

        # ---- L4: Specialist claim authoring ---------------------------------
        state.advance("analyzing")
        agents_by_key = {
            a.key: a.factory(model_name=agent_models.get(a.key, default_model)) for a in assigned
        }
        claims = run_specialists(plan, ctx, agents_by_key, callbacks=cb)
        specialists_by_label = {agent.label: agent for agent in agents_by_key.values()}
        for c in claims:
            yield {"event": "claim", "data": c.model_dump()}

        # ---- L4 + L4c: Adversarial review sweeps under the Crux controller --
        open_cruxes = []
        if claims and valid_ids:
            review = AdversarialReview(specialists_by_label, callbacks=cb)
            for sweep_n in range(1, max_passes + 1):
                if state.budget.exhausted:
                    break
                state.advance("reviewing")
                state.sweep = sweep_n
                log = review.sweep(topic, ctx, digest, valid_ids, sweep_n)
                yield {"event": "sweep", "data": {
                    "sweep": log.sweep_number,
                    "filed": log.filed,
                    "admitted": log.admitted,
                    "dropped": log.dropped,
                    "revisions": log.revisions,
                    "outcomes": [{
                        "challenge_id": o.challenge.challenge_id,
                        "target_claim": o.challenge.target_claim,
                        "challenger": o.challenge.challenger,
                        "kind": o.challenge.kind,
                        "severity": o.challenge.severity,
                        "response": o.response_action,
                        "pre_confidence": o.pre_confidence,
                        "post_confidence": o.post_confidence,
                        "status": o.new_status,
                    } for o in log.outcomes],
                }}

                # L4c Crux & Sensitivity controller: next sweep | re-gather | stable
                state.advance("crux")
                cd = crux_ctl.next_action(
                    ctx, plan,
                    sweep=sweep_n, max_sweeps=max_passes,
                    reentry_cycle=state.reentry_cycle, max_reentries=state.max_reentries,
                )
                yield {"event": "crux", "data": {
                    "action": cd.action,
                    "reason": cd.reason,
                    "cruxes": [c.model_dump() for c in cd.cruxes],
                }}

                fixed_point = (log.admitted == 0 and log.revisions == 0)
                if cd.action == "stable" or fixed_point:
                    break
                # next_sweep -> loop again

            review.finalize(ctx)
            open_cruxes = [c for c in crux_ctl.find_cruxes(ctx) if c.unresolved]

        # ---- L5a: Contradiction resolution ----------------------------------
        state.advance("resolving")
        contradictions = resolve_contradictions(ctx)
        if contradictions:
            yield {"event": "contradictions", "data": [c.model_dump() for c in contradictions]}

        # ---- L5: Decision (confidence-weighted aggregation + synthesis) -----
        state.advance("deciding")
        decision, agg = decide(
            topic, ctx, plan, contradictions, report, open_cruxes=open_cruxes,
            synthesizer_model=default_model, use_llm=use_llm, callbacks=cb,
        )

        # ---- L6: Evaluator (rubric) + failure-routed retry ------------------
        state.advance("evaluating")
        expected_dissent = {d.claim_id for d in agg.dissent}
        card = evaluate(
            decision, ctx, question_type=plan.question_type,
            aggregate_confidence=agg.decision_confidence,
            expected_dissent_ids=expected_dissent, sufficiency=report,
        )
        while not card.passed and state.can_retry():
            state.advance("retrying")
            state.retries += 1
            target = retry_target(card)
            # grounding -> re-narrate (LLM); calibration -> recompute math (no LLM).
            # (Completeness re-gather is gone: researchers own fetching and the plan
            # declares no labels to target, so a failed card just re-decides.)
            decision, agg = decide(
                topic, ctx, plan, contradictions, report, open_cruxes=open_cruxes,
                synthesizer_model=default_model,
                use_llm=use_llm and target != "calibration", callbacks=cb,
            )
            expected_dissent = {d.claim_id for d in agg.dissent}
            card = evaluate(
                decision, ctx, question_type=plan.question_type,
                aggregate_confidence=agg.decision_confidence,
                expected_dissent_ids=expected_dissent, sufficiency=report,
            )
        yield {"event": "evaluation",
               "data": card.model_dump() | {"retries": state.retries}}

        yield {"event": "decision", "data": decision.model_dump()}
        yield {"event": "verdict", "data": {"text": decision.answer}}

        snapshot = ctx.snapshot()
        yield {"event": "evidence_snapshot", "data": snapshot}

        trace.set_output({
            "confidence": decision.confidence,
            "confidence_kind": decision.confidence_kind,
            "answer": decision.answer,
            "dissent": len(decision.unresolved_dissent),
            "open_cruxes": len(decision.open_cruxes),
        })

    clear_context(session_id)
    yield {"event": "end", "data": {}}
