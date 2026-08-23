"""Orchestrator (L0) — roster selection + the outer lifecycle.

The new top of the pipeline and the **outer-lifecycle controller**: its question
is *"is the whole run acceptable?"* (contrast the Crux, whose question is *"is
deliberation done?"*). Two jobs:

* **Roster selection** — given the question + type, score every expert and
  researcher in the catalog and admit only the relevant ones, dropping the clearly
  irrelevant (a commodities analyst has no business on a striker-valuation
  question). Selection is **conservative** (err toward inclusion on a borderline
  agent — a missing perspective is worse than one extra specialist's bounded cost)
  and **re-admittable** (a later re-gather can pull a dropped agent back in, so
  drops are logged with a reason).
* **Lifecycle** — own the global token/cost budget, the retry decision (fed by the
  Evaluator), and the single planner re-entry point shared with re-gather. The
  cost figure is read from the Infra/State plane (Langfuse), mirrored locally in
  ``RunState.token_cost`` for in-flight enforcement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from server.core.agents.teams import AgentInfo
from server.core.evidence.models import QuestionType, RosterSelection

_WORD_RE = re.compile(r"[a-z0-9]+")

# A tiny stoplist so generic words don't make every agent look "relevant".
_STOP = {
    "the", "a", "an", "is", "are", "of", "to", "in", "for", "and", "or", "this",
    "that", "with", "be", "than", "who", "what", "which", "how", "do", "does",
    "should", "we", "i", "it", "team", "player", "season", "club",
}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOP}


def score_specialist(question: str, agent: AgentInfo) -> float:
    """Relevance of one specialist to the question (keyword overlap, [0, ~1]).

    Deterministic and conservative — used only to *drop the clearly irrelevant*,
    not to finely rank. An agent whose key/label/description shares no salient term
    with the question scores 0 and becomes a drop candidate.
    """
    q = _tokens(question)
    if not q:
        return 1.0  # nothing to go on -> keep everyone
    profile = _tokens(f"{agent.key} {agent.label} {agent.description}")
    if not profile:
        return 0.5
    overlap = len(q & profile)
    return overlap / max(1, len(profile))


def _admit_researchers(question: str) -> tuple[list[str], list[dict]]:
    """Admit the researcher *pool* (every researcher key) for this question.

    Unlike specialists, the relevant-researcher *selection* is the Planner's job
    (it picks the subset and briefs each — what the question explicitly asks for),
    so the roster simply exposes the whole, small, fixed researcher team. Keyword
    overlap on their narrow descriptions is too blunt to drop on here. ``question``
    is accepted for symmetry / future scoring.
    """
    from server.core.agents.researchers.registry import get_researchers

    return list(get_researchers()), []


def select_roster(
    question: str,
    agents: list[AgentInfo],
    *,
    question_type: QuestionType = "scouting",
    min_specialists: int = 2,
) -> RosterSelection:
    """Score the catalog; admit the relevant specialists + researchers.

    Conservative admission: an agent is dropped only if it shares no salient term
    with the question AND dropping it still leaves at least ``min_specialists`` —
    a missing perspective is worse than an extra specialist's bounded cost.
    """
    scored = [(a, score_specialist(question, a)) for a in agents]
    relevant = [a for a, s in scored if s > 0.0]
    dropped: list[dict] = []

    if len(relevant) >= min_specialists:
        admitted = relevant
        for a, s in scored:
            if s <= 0.0:
                dropped.append({"agent": a.key, "reason": "no salient overlap with the question"})
    else:
        # Too aggressive — keep everyone rather than strand a perspective.
        admitted = [a for a, _ in scored]

    researchers, r_dropped = _admit_researchers(question)
    return RosterSelection(
        question_type=question_type,
        admitted_specialists=[a.key for a in admitted],
        admitted_researchers=researchers,
        dropped=dropped + r_dropped,
    )


# --- lifecycle: budget + run state ------------------------------------------


@dataclass
class Budget:
    """The global token/cost ceiling the Orchestrator debits (read from Langfuse)."""

    ceiling: float = 0.0      # 0 => unbounded
    spent: float = 0.0

    def debit(self, amount: float) -> None:
        self.spent += max(0.0, amount)

    @property
    def exhausted(self) -> bool:
        return self.ceiling > 0 and self.spent >= self.ceiling

    @property
    def remaining(self) -> float:
        return float("inf") if self.ceiling <= 0 else max(0.0, self.ceiling - self.spent)


# Phases mirror the design's RunState.phase enum (one per pipeline stage).
RunPhase = str  # "selecting" | "planning" | ... | "evaluating" | "retrying"


@dataclass
class RunState:
    """The Orchestrator's view of one run's lifecycle (the design's RunState)."""

    run_id: str
    phase: RunPhase = "selecting"
    sweep: int = 0
    max_sweeps: int = 3
    reentry_cycle: int = 0          # shared by re-gather (crux) + completeness retry
    max_reentries: int = 2
    retries: int = 0
    max_retries: int = 2
    token_cost: float = 0.0
    model_routing: dict[str, str] = field(default_factory=dict)
    trace_id: str = ""
    budget: Budget = field(default_factory=Budget)

    def advance(self, phase: RunPhase) -> None:
        self.phase = phase

    def can_reenter(self) -> bool:
        """Shared gate for both re-entry loops, under the global budget."""
        return self.reentry_cycle < self.max_reentries and not self.budget.exhausted

    def can_retry(self) -> bool:
        return self.retries < self.max_retries and not self.budget.exhausted
