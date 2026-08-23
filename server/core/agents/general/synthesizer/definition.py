"""L5 Synthesizer — narrates the resolved ledger into a final answer.

This is the *thin* half of v1's split judge: the deterministic Aggregator does
the math (decision confidence, supporting claims, dissent); the Synthesizer only
writes the rationale, strictly from resolved claims, their confidences, the quant
forecasts, and the surviving dissent. It never scores "agreement" and never
invents a number.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from server.core.agents.base import load_prompt
from server.core.evidence.models import DeliverableSpec
from server.core.prompts import get_text

PROMPT_PATH = Path(__file__).parent / "prompt.md"


def create_synthesizer_model(model_name: str = "gpt-4o-mini", temperature: float = 0.0):
    from server.core.agents.models import resolve_model, supports_temperature

    resolved = resolve_model(model_name)
    kwargs = {"model": resolved, "streaming": True}
    if supports_temperature(resolved):
        kwargs["temperature"] = temperature
    model = ChatOpenAI(**kwargs)
    prompt = get_text("synthesizer", fallback=load_prompt(PROMPT_PATH))
    return model, prompt


def _deliverable_instruction(deliverable: DeliverableSpec | None) -> str:
    """The explicit shape contract the answer must satisfy, in plain words."""
    if deliverable is None:
        deliverable = DeliverableSpec()
    parts = [f"Your answer MUST take this shape: {deliverable.format}."]
    if deliverable.cardinality:
        parts.append(
            f"Produce exactly {deliverable.cardinality} item(s). If the evidence "
            f"supports fewer, say so explicitly and give as many as the evidence "
            f"supports — never pad with unsupported items."
        )
    if deliverable.subjects:
        parts.append(f"Cover these subjects: {', '.join(deliverable.subjects)}.")
    if deliverable.dimensions:
        parts.append(
            f"Grade/rank on these dimensions: {', '.join(deliverable.dimensions)}."
        )
    if deliverable.success_criteria:
        parts.append(f"A complete answer: {deliverable.success_criteria}.")
    return " ".join(parts)


def synthesize(
    question: str,
    aggregate_block: str,
    *,
    deliverable: DeliverableSpec | None = None,
    model_name: str = "gpt-4o-mini",
    callbacks: list | None = None,
) -> str:
    """Write the final recommendation from a rendered aggregate block.

    ``deliverable`` is the requested answer shape (see ``DeliverableSpec``).
    Satisfying it — the format, the item count, the grading dimensions — is the
    synthesizer's primary obligation, subordinate only to staying grounded in the
    resolved ledger.
    """
    model, prompt = create_synthesizer_model(model_name=model_name)
    user = (
        f"QUESTION:\n{question}\n\n"
        f"REQUIRED DELIVERABLE:\n{_deliverable_instruction(deliverable)}\n\n"
        f"RESOLVED LEDGER + COMPUTED DECISION:\n{aggregate_block}\n\n"
        "Write the final answer now, in exactly the required deliverable shape."
    )
    template = ChatPromptTemplate.from_messages(
        [SystemMessage(content=prompt.text), HumanMessage(content=user)]
    )
    if prompt.client is not None:
        template.metadata = {"langfuse_prompt": prompt.client}
    cfg = {"callbacks": callbacks, "run_name": "synthesizer"} if callbacks else {}
    try:
        result = (template | model).invoke({}, config=cfg)
        return result.content if isinstance(result.content, str) else str(result.content)
    except Exception:  # noqa: BLE001 - never let the narrator failure sink a computed decision
        return ""
