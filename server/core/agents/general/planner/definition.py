"""L1 Research Planner.

Evolves v1's smart-routing Router into a planner that also plans *retrieval*. It
turns a question into a :class:`~server.core.evidence.models.ResearchPlan`:
question type, entities, the concrete evidence to gather, which specialists run,
and which quant models to compute — all *before* anyone reasons.

Two-stage design for determinism + robustness:

1. **Sketch (cognition).** An LLM (structured output) — or a keyword heuristic
   fallback when no LLM is configured — classifies the question, extracts
   entities/seasons, picks specialists, and picks quant models. This is the only
   judgement call, and it is small and well-typed.
2. **Expansion (mechanism).** :func:`_expand_requests` deterministically turns the
   sketch into concrete, valid ``EvidenceRequest``s using known tool templates,
   so tool names/args are always correct and reproducible.

Backwards compatible with the old router: ``selected_specialists`` on the plan is
exactly the routing decision, so planning-off callers still get agent selection.
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from server.core.agents.base import load_prompt
from server.core.agents.teams import AgentInfo
from server.core.evidence.models import (
    DeliverableSpec,
    QuestionType,
    ResearchPlan,
)
from server.core.prompts import get_text

PROMPT_PATH = Path(__file__).parent / "prompt.md"

# Latest season in the warehouse; used when a question names no season.
DEFAULT_SEASON = "2025-2026"

# Canonical club names (+ common aliases) for heuristic entity extraction. The
# warehouse tools fuzzy-match, so aliases need only be close.
_TEAM_ALIASES: dict[str, str] = {
    "arsenal": "Arsenal",
    "barcelona": "Barcelona",
    "barca": "Barcelona",
    "bayern": "Bayern Munich",
    "bayern munich": "Bayern Munich",
    "dortmund": "Dortmund",
    "man city": "Manchester City",
    "manchester city": "Manchester City",
    "city": "Manchester City",
    "man united": "Manchester United",
    "man utd": "Manchester United",
    "manchester united": "Manchester United",
    "united": "Manchester United",
    "psg": "Paris Saint Germain",
    "paris saint germain": "Paris Saint Germain",
    "paris": "Paris Saint Germain",
    "real madrid": "Real Madrid",
    "madrid": "Real Madrid",
}


class ResearcherBrief(BaseModel):
    """A one-line instruction for a single researcher (key -> what to gather)."""

    researcher: str
    brief: str


class PlanSketch(BaseModel):
    """The small, well-typed judgement the planner LLM (or heuristic) makes."""

    question_type: QuestionType = "scouting"
    teams: list[str] = Field(default_factory=list)
    players: list[str] = Field(default_factory=list)
    seasons: list[str] = Field(default_factory=list)
    assigned_specialists: list[str] = Field(default_factory=list)
    # The researchers (by key) relevant to this question, and a one-line brief
    # (statement) for each telling it what to gather. The researcher decides its
    # own MCP calls from the brief. A list (not a dict) because OpenAI strict
    # structured output rejects open-ended maps (additionalProperties).
    assigned_researchers: list[str] = Field(default_factory=list)
    researcher_briefs: list[ResearcherBrief] = Field(default_factory=list)
    quant_models: list[str] = Field(default_factory=list)
    # The desired answer shape, derived from the question (see ``DeliverableSpec``).
    # Separate from ``question_type``: that axis is epistemic (how confidence is
    # treated); this axis is the deliverable (what the answer looks like).
    deliverable: DeliverableSpec = Field(default_factory=DeliverableSpec)
    reasoning: str = ""

    def briefs_by_key(self) -> dict[str, str]:
        """The researcher briefs as a key -> statement mapping."""
        return {b.researcher: b.brief for b in self.researcher_briefs}


# --- heuristic (offline / fallback) -----------------------------------------

_PROB_RE = re.compile(r"\b(p\(|probability|chance|odds|likely|over/under|over |under )", re.I)
_COMP_RE = re.compile(r"\b(stronger|better than|compare|versus|\bvs\b|who wins|beat)\b", re.I)
_VAL_RE = re.compile(r"\b(sign|buy|worth|value|recruit|good season|percentile|transfer)\b", re.I)
_SEASON_RE = re.compile(r"\b(20\d{2}-20\d{2}|20\d{2}/20\d{2})\b")
# "top 5", "5 best", "rank the 3", "list 10" — the count a list-shaped answer wants.
_CARD_RE = re.compile(
    r"\b(?:top|best|first|rank(?:ed)?|list)\s+(\d{1,3})\b"
    r"|\b(\d{1,3})\s+(?:most|best|top|strongest|players|teams)\b",
    re.I,
)
_LIST_RE = re.compile(r"\b(rank|ranking|list|top|table|tier|order|leaderboard)\b", re.I)


def _extract_cardinality(question: str) -> int:
    m = _CARD_RE.search(question)
    if not m:
        return 0
    raw = m.group(1) or m.group(2)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _heuristic_deliverable(question: str) -> DeliverableSpec:
    """A best-effort deliverable shape with no LLM: detect list/ranking + a count.

    The LLM planner writes a far richer spec; this only has to keep offline runs
    and the fallback path from collapsing every question to a single verdict.
    """
    n = _extract_cardinality(question)
    if n > 1 or _LIST_RE.search(question):
        count = n or 0
        head = f"a ranked list of {count} item(s)" if count else "a ranked list"
        return DeliverableSpec(
            format=f"{head}, each with a grade and the key stats behind it",
            cardinality=count,
            success_criteria=(
                f"names {count} ranked items with a grade for each"
                if count else "presents the items in ranked order with grades"
            ),
        )
    return DeliverableSpec()  # default: a direct, well-grounded recommendation


def _classify(question: str) -> QuestionType:
    q = question.lower()
    if _PROB_RE.search(q):
        return "probability"
    if _VAL_RE.search(q):
        return "valuation"
    if _COMP_RE.search(q):
        return "comparison"
    return "scouting"


def _extract_teams(question: str) -> list[str]:
    q = question.lower()
    found: list[str] = []
    consumed_spans: list[tuple[int, int]] = []
    # Longest aliases first so "manchester city" wins over "city"; track matched
    # spans so an alias nested inside an already-matched one doesn't double-count.
    for alias in sorted(_TEAM_ALIASES, key=len, reverse=True):
        start = q.find(alias)
        if start == -1:
            continue
        end = start + len(alias)
        if any(s <= start and end <= e for s, e in consumed_spans):
            continue
        canonical = _TEAM_ALIASES[alias]
        if canonical not in found:
            found.append(canonical)
        consumed_spans.append((start, end))
    return found


def _extract_seasons(question: str) -> list[str]:
    return [m.group(0).replace("/", "-") for m in _SEASON_RE.finditer(question)]


def _quant_for(qtype: QuestionType, n_teams: int, n_players: int) -> list[str]:
    models: list[str] = []
    if n_teams >= 2:
        if qtype == "probability":
            models += ["win_probability", "goals"]
        elif qtype == "comparison":
            models += ["strength", "win_probability"]
        elif qtype in ("scouting", "valuation"):
            models += ["strength"]
    if n_players >= 1 and qtype in ("valuation", "scouting"):
        models.append("player_value")
    return list(dict.fromkeys(models))


def heuristic_sketch(question: str, agents: list[AgentInfo]) -> PlanSketch:
    qtype = _classify(question)
    teams = _extract_teams(question)
    seasons = _extract_seasons(question) or [DEFAULT_SEASON]
    return PlanSketch(
        question_type=qtype,
        teams=teams,
        players=[],  # players are hard to extract without an LLM; left to that path
        seasons=seasons,
        assigned_specialists=[a.key for a in agents],
        quant_models=_quant_for(qtype, len(teams), 0),
        deliverable=_heuristic_deliverable(question),
        reasoning="heuristic plan (no LLM): keyword classification + alias matching.",
    )


# --- LLM sketch -------------------------------------------------------------


def _format_agents(agents: list[AgentInfo]) -> str:
    return "\n".join(
        f"- {a.key}: {a.description or a.label}" for a in agents
    )


def _format_researchers(researcher_keys: list[str]) -> str:
    from server.core.agents.researchers.registry import get_researcher

    lines = []
    for key in researcher_keys:
        info = get_researcher(key)
        if info is not None:
            lines.append(f"- {key}: {info.description}")
    return "\n".join(lines)


def _llm_sketch(
    question: str,
    agents: list[AgentInfo],
    researcher_keys: list[str],
    callbacks: list | None,
) -> PlanSketch:
    prompt = get_text("planner", fallback=load_prompt(PROMPT_PATH))
    user = (
        f"QUESTION:\n{question}\n\n"
        f"AVAILABLE SPECIALISTS (key: domain):\n{_format_agents(agents)}\n\n"
        f"AVAILABLE RESEARCHERS (key: what they gather):\n"
        f"{_format_researchers(researcher_keys)}\n\n"
        "Pick the specialists AND the researchers relevant to the question. For "
        "each chosen researcher, add an entry to `researcher_briefs` with its "
        "`researcher` key and a one-line `brief` telling it exactly what to gather "
        "for this question.\n\n"
        "Also fill `deliverable` — the SHAPE of the answer the question asks for, "
        "independent of `question_type`. Write `format` as a free-form description "
        "of what the answer should look like (a single recommendation, a ranked "
        "list, a table, a yes/no with a number…). If the question asks for several "
        "items (e.g. 'top 5'), set `cardinality` to that number; if it names the "
        "subjects to assess, list them in `subjects`; if it asks to grade/rank on "
        "specific criteria, list those in `dimensions`. Set `success_criteria` to "
        "one line describing what a complete answer to THIS question must contain. "
        "Return the plan."
    )
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
    structured = model.with_structured_output(PlanSketch)
    template = ChatPromptTemplate.from_messages(
        [SystemMessage(content=prompt.text), HumanMessage(content=user)]
    )
    if prompt.client is not None:
        template.metadata = {"langfuse_prompt": prompt.client}
    cfg = {"callbacks": callbacks, "run_name": "research-planner"} if callbacks else {}
    return (template | structured).invoke({}, config=cfg)


# --- expansion (deterministic) ----------------------------------------------


def build_research_plan(
    question: str,
    agents: list[AgentInfo],
    *,
    researchers: list[str] | None = None,
    use_llm: bool = True,
    callbacks: list | None = None,
) -> ResearchPlan:
    """Produce a full ResearchPlan for ``question``.

    ``researchers`` is the admitted researcher pool (keys from the roster); the
    planner selects the relevant subset and writes a brief for each. The selected
    researchers — not the planner — decide their own MCP calls at L2.
    """
    researcher_pool = list(researchers or [])
    sketch: PlanSketch
    if use_llm:
        try:
            sketch = _llm_sketch(question, agents, researcher_pool, callbacks)
        except Exception:  # noqa: BLE001 - any LLM/parse failure -> deterministic plan
            sketch = heuristic_sketch(question, agents)
    else:
        sketch = heuristic_sketch(question, agents)

    valid_keys = {a.key for a in agents}
    assigned = [k for k in sketch.assigned_specialists if k in valid_keys] or list(valid_keys)
    seasons = sketch.seasons or [DEFAULT_SEASON]
    sketch.seasons = seasons

    # Select researchers: the planner's choice, constrained to the admitted pool;
    # fall back to the whole pool. Every selected researcher gets a brief.
    assigned_researchers = [k for k in sketch.assigned_researchers if k in researcher_pool] \
        or researcher_pool
    briefs = sketch.briefs_by_key()
    researcher_briefs = {
        k: (briefs.get(k) or question) for k in assigned_researchers
    }

    entities = [*sketch.teams, *sketch.players]

    # Deliverable shape: the planner's spec, with named players backfilled as
    # subjects when it left them implicit (so an entity-aware decision can score
    # them). Subjects stay empty for "top N <unnamed>" — those emerge from evidence.
    deliverable = sketch.deliverable or DeliverableSpec()
    if not deliverable.subjects and sketch.players:
        deliverable.subjects = list(sketch.players)

    return ResearchPlan(
        question=question,
        question_type=sketch.question_type,
        deliverable=deliverable,
        entities=entities,
        # No planner-authored evidence "needs": each assigned researcher decides
        # what to fetch from the prompt + its own tools (see researchers/).
        required_evidence=[],
        assigned_specialists=assigned,
        assigned_researchers=assigned_researchers,
        researcher_briefs=researcher_briefs,
        quant_models=sketch.quant_models,
        reasoning=sketch.reasoning,
    )
