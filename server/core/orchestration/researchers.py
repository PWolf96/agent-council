"""Researcher team dispatch (L2) — run the planner-selected researchers.

The Planner (L1) selects researchers and writes a brief for each; this module
turns that into work: for every assigned researcher it builds the agent, hands it
the prompt + its brief, and lets the **researcher decide and execute its own MCP
calls**, writing gather-only records into the Evidence Pool.

Hard boundary preserved from the old retriever pool: **researchers gather
evidence; they never make claims** — this module only writes to the Evidence
Pool, never the Claim Ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.core.evidence.models import EvidenceRecord, ResearchPlan
from server.core.evidence.store import EvidenceContext


@dataclass
class ResearchResult:
    records: list[EvidenceRecord] = field(default_factory=list)
    cache_stats: dict = field(default_factory=dict)
    requests_run: int = 0
    failures: list[str] = field(default_factory=list)   # empty/errored records (retryable)
    negatives: list[str] = field(default_factory=list)  # "searched, provably absent"
    workers: int = 0                                     # researchers dispatched
    breadth: int = 0                                     # calls made across the team


def dispatch_research(
    plan: ResearchPlan,
    ctx: EvidenceContext,
    *,
    researchers: dict,
    model_name: str | None = None,
    callbacks: list | None = None,
) -> ResearchResult:
    """Run every assigned researcher over the prompt; collect their evidence.

    ``researchers`` is the registry map (key -> ``ResearcherInfo``). Researchers
    run in ``plan.assigned_researchers`` order so evidence ids stay as stable as
    an LLM-in-the-loop allows. Idempotent at the pool level (dedup on args_hash).
    """
    assigned = plan.assigned_researchers or list(researchers)
    run_keys = [k for k in assigned if k in researchers]
    result = ResearchResult(workers=len(run_keys))

    for key in run_keys:
        agent = researchers[key].create(model_name)
        brief = plan.researcher_briefs.get(key) or plan.question
        run = agent.gather(plan.question, brief, ctx, callbacks=callbacks)
        result.breadth += run.calls
        for record in run.records:
            result.records.append(record)
            result.requests_run += 1
            tag = record.covers[0] if record.covers else key
            if record.is_negative_result:
                result.negatives.append(tag)
            elif record.is_empty:
                result.failures.append(tag)

    return result
