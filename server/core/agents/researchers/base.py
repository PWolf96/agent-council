"""Researcher agents (L2) — planner-selected, MCP-driven evidence gatherers.

The Planner (L1) selects the relevant researchers and hands each one a *brief*
(a statement of what to find for this question). Each researcher then **decides
which MCP calls to make** — it is an LLM agent, not a fixed script — executes
them through the :data:`~server.mcp.mcp_client`, and writes the results as
``EvidenceRecord``s into the immutable Evidence Pool. Its tool catalog is
discovered live from the bound MCP server (``tools/list``), falling back to the
researcher's static ``tool_specs`` when the server is unreachable.

Hard boundary (unchanged from the old retriever pool): **researchers gather
evidence; they never make claims.** This module's whole surface is
``EvidencePool`` writes — it cannot touch the Claim Ledger.

The decision step uses ``with_structured_output(ResearchActions)`` at
temperature 0 so the same brief yields the same calls; execution order is fixed
so evidence ids stay as stable as an LLM-in-the-loop allows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from server.core.evidence.models import EvidenceRecord, StrengthTier
from server.core.evidence.store import EvidenceContext
from server.mcp import is_empty_payload, is_negative_result, mcp_client


# --- LLM-facing structured output -------------------------------------------


class MCPCall(BaseModel):
    """One MCP tool call the researcher decided to make.

    ``args_json`` is a JSON object encoded as a string (not a free-form object)
    so the schema stays valid under strict structured-output modes.
    """

    tool: str = Field(description="MCP tool name — must be one offered to you below.")
    args_json: str = Field(
        default="{}",
        description='Tool arguments as a JSON object string, e.g. {"team":"Arsenal"}.',
    )
    covers: list[str] = Field(
        default_factory=list,
        description="Which of your briefed evidence-need labels this call helps cover.",
    )

    def args(self) -> dict:
        try:
            parsed = json.loads(self.args_json or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}


class ResearchActions(BaseModel):
    """The set of MCP calls a researcher chooses to run for its brief."""

    calls: list[MCPCall] = Field(default_factory=list)


# --- result -----------------------------------------------------------------


@dataclass
class ResearcherRun:
    """What one researcher produced in a single dispatch."""

    key: str
    records: list[EvidenceRecord] = field(default_factory=list)
    calls: int = 0


# --- the agent --------------------------------------------------------------


def _render_tool_specs(tool_specs: list[dict]) -> str:
    lines = []
    for spec in tool_specs:
        schema = json.dumps(spec.get("args_schema", {}), default=str)
        lines.append(f"- {spec['name']}: {spec.get('description', '')}\n    args: {schema}")
    return "\n".join(lines)


class ResearcherAgent:
    """An LLM that decides + executes MCP calls for its domain, gather-only."""

    def __init__(
        self,
        model: ChatOpenAI,
        system_prompt: str,
        *,
        key: str,
        label: str,
        server: str,
        tool_specs: list[dict],
        strength_tier: StrengthTier,
        source_trust: float,
        prompt_client=None,
        catalog_tool: str | None = None,
    ):
        self._model = model
        self._system_prompt = system_prompt
        self.key = key
        self.label = label
        self.server = server
        self.tool_specs = tool_specs
        self.strength_tier = strength_tier
        self.source_trust = source_trust
        self._prompt_client = prompt_client
        # Optional no-arg discovery tool (e.g. ``get_catalog``) fetched *before*
        # the decision so a single-shot call can use confirmed column names in
        # ``select``/``order_by`` instead of defensively omitting them.
        self.catalog_tool = catalog_tool

    def _fetch_catalog(self) -> str | None:
        """Pre-fetch the server's schema catalog, rendered for the prompt.

        Returns ``None`` when no catalog tool is declared or the call fails — the
        decision then proceeds without it (and the prompt's "omit columns you have
        not confirmed" safety rule still applies).
        """
        if not self.catalog_tool:
            return None
        payload = mcp_client.call_tool(self.server, self.catalog_tool, {})
        if isinstance(payload, dict) and payload.get("error"):
            return None
        return json.dumps(payload, default=str)

    def _decide(
        self,
        question: str,
        brief: str,
        tool_specs: list[dict],
        callbacks: list | None,
        catalog: str | None = None,
    ) -> ResearchActions:
        catalog_block = (
            "DATASET CATALOG (authoritative — these exact column names are safe to "
            f"use in `select` and `order_by`):\n{catalog}\n\n"
            if catalog
            else ""
        )
        user = (
            f"QUESTION:\n{question}\n\n"
            f"YOUR BRIEF:\n{brief}\n\n"
            f"{catalog_block}"
            f"MCP TOOLS AVAILABLE TO YOU (server '{self.server}'):\n"
            f"{_render_tool_specs(tool_specs)}\n\n"
            "Decide the MCP calls that gather the evidence this question needs from "
            "your domain. Only call the tools listed above; pass concrete arguments. "
            "Do not analyse or conclude — only gather. Return an empty list only if "
            "nothing in your domain applies."
        )
        system = SystemMessage(
            content=self._system_prompt,
            additional_kwargs={"cache_control": {"type": "ephemeral"}},
        )
        template = ChatPromptTemplate.from_messages([system, HumanMessage(content=user)])
        if self._prompt_client is not None:
            template.metadata = {"langfuse_prompt": self._prompt_client}
        structured = self._model.with_structured_output(ResearchActions)
        cfg = {"callbacks": callbacks, "run_name": f"research:{self.key}"} if callbacks else {}
        try:
            return (template | structured).invoke({}, config=cfg)
        except Exception:  # noqa: BLE001 - a decision failure yields no calls (a gap)
            return ResearchActions()

    def gather(
        self,
        question: str,
        brief: str,
        ctx: EvidenceContext,
        *,
        callbacks: list | None = None,
    ) -> ResearcherRun:
        """Decide MCP calls, execute them, write evidence records. Gather-only."""
        # Discover the server's live tool catalog; fall back to the static specs
        # when the server is unreachable (offline dev / tests).
        tool_specs = mcp_client.list_tools(self.server) or self.tool_specs
        catalog = self._fetch_catalog()
        actions = self._decide(question, brief, tool_specs, callbacks, catalog=catalog)
        run = ResearcherRun(key=self.key, calls=len(actions.calls))

        for call in actions.calls:
            tool = call.tool
            args = call.args()
            payload = mcp_client.call_tool(self.server, tool, args)
            record = ctx.pool.add(
                source_tool=f"mcp:{self.server}.{tool}",
                args=args,
                payload=payload,
                strength_tier=self.strength_tier,
                source_trust=self.source_trust,
                provenance=f"mcp:{self.server}",
                covers=call.covers,
                is_empty=is_empty_payload(payload),
                is_negative_result=is_negative_result(payload),
            )
            run.records.append(record)
        return run


def build_researcher(
    *,
    key: str,
    label: str,
    server: str,
    tool_specs: list[dict],
    prompt_path: Path,
    strength_tier: StrengthTier,
    source_trust: float,
    model_name: str | None = None,
    temperature: float = 0.0,
    catalog_tool: str | None = None,
) -> ResearcherAgent:
    """Construct a researcher from its persona prompt + MCP tool catalog."""
    from server.core.agents.models import resolve_model, supports_temperature
    from server.core.prompts import get_text

    from server.core.agents.base import load_prompt

    prompt = get_text(f"researcher/{key}", fallback=load_prompt(prompt_path))
    resolved = resolve_model(model_name)
    kwargs = {"model": resolved}
    if supports_temperature(resolved):
        kwargs["temperature"] = temperature
    model = ChatOpenAI(**kwargs)
    return ResearcherAgent(
        model,
        prompt.text,
        key=key,
        label=label,
        server=server,
        tool_specs=tool_specs,
        strength_tier=strength_tier,
        source_trust=source_trust,
        prompt_client=prompt.client,
        catalog_tool=catalog_tool,
    )
