from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver


def load_prompt(prompt_path: Path) -> str:
    return Path(prompt_path).read_text(encoding="utf-8").strip()


class SimpleAgent:

    def __init__(self, model: ChatOpenAI, system_prompt: str, prompt_client=None):
        self._model = model
        self._system_prompt = system_prompt
        # LangfusePromptClient (or None): lets the generation link to the managed
        # persona prompt in Langfuse.
        self._prompt_client = prompt_client

    def invoke(self, state: dict, config: dict | None = None) -> dict:
        user_msg = state["messages"][-1]
        if isinstance(user_msg, dict):
            user_msg = HumanMessage(content=user_msg["content"])

        system = SystemMessage(
            content=self._system_prompt,
            additional_kwargs={"cache_control": {"type": "ephemeral"}},
        )
        # Run through a ChatPromptTemplate (a chain step) so the persona prompt
        # links to the generation in Langfuse: the CallbackHandler registers the
        # template's ``langfuse_prompt`` metadata and links the LLM call it wraps.
        # ``system`` is a literal message, so braces in the persona text are not
        # mistaken for template variables.
        template = ChatPromptTemplate.from_messages(
            [system, MessagesPlaceholder("messages")]
        )
        if self._prompt_client is not None:
            template.metadata = {"langfuse_prompt": self._prompt_client}
        chain = template | self._model

        result = chain.invoke({"messages": [user_msg]}, config=config)
        return {"messages": state["messages"] + [result]}


class ClaimWriterAgent:
    """A v2 specialist: reads shared evidence, emits typed, owned, cited claims.

    Unlike v1's ReAct agents, a claim-writer does **no** retrieval — retrieval is
    decoupled into L2. It is handed a digest of the shared Evidence Store and
    must ground every claim in one or more evidence ids (uncited claims are
    dropped here and rejected by the ledger). It runs at temperature 0 so the
    same evidence yields the same claims.

    The same object also owns its claims through the deliberation loop: it can
    :meth:`respond` to a challenge by citing, conceding, or revising.
    """

    def __init__(
        self,
        model: ChatOpenAI,
        system_prompt: str,
        *,
        dimension: str,
        key: str,
        label: str,
        prompt_client=None,
    ):
        self._model = model
        self._system_prompt = system_prompt
        self.dimension = dimension
        self.key = key
        self.label = label
        self._prompt_client = prompt_client

    def _template(self, user_text: str) -> ChatPromptTemplate:
        system = SystemMessage(
            content=self._system_prompt,
            additional_kwargs={"cache_control": {"type": "ephemeral"}},
        )
        template = ChatPromptTemplate.from_messages(
            [system, HumanMessage(content=user_text)]
        )
        if self._prompt_client is not None:
            template.metadata = {"langfuse_prompt": self._prompt_client}
        return template

    def write_claims(
        self,
        question: str,
        evidence_digest: str,
        valid_ids: set[str],
        callbacks: list | None = None,
    ) -> list:
        """Produce grounded :class:`ClaimDraft`s, keeping only cited claims."""
        from server.core.evidence.models import SpecialistClaims

        user = (
            f"QUESTION:\n{question}\n\n"
            f"SHARED EVIDENCE (cite ids exactly as shown, e.g. E03):\n{evidence_digest}\n\n"
            "Write 2-4 claims strictly within your domain. Every claim MUST cite at "
            "least one evidence id from above. Do not assert anything you cannot cite."
        )
        structured = self._model.with_structured_output(SpecialistClaims)
        cfg = {"callbacks": callbacks, "run_name": f"claims:{self.key}"} if callbacks else {}
        result = (self._template(user) | structured).invoke({}, config=cfg)

        drafts = []
        for draft in result.claims:
            cited = [e for e in draft.evidence_ids if e in valid_ids]
            if not cited:
                continue  # uncited -> rejected by L2
            draft.evidence_ids = cited
            drafts.append(draft)
        return drafts

    def review(
        self,
        question: str,
        others: list,
        evidence_digest: str,
        valid_ids: set[str],
        callbacks: list | None = None,
    ) -> list:
        """Critic hat: file evidence-bound objections on claims this agent does NOT own.

        The single-role sweep folds the old standalone Challenger into the
        specialist: every specialist scans the claims it doesn't own and files a
        typed :class:`ChallengeDraft` on any it disagrees with. The
        admissibility gate (in the deliberation loop) drops objections that don't
        cite real evidence or name a concrete inference flaw.
        """
        from server.core.evidence.models import ChallengeBatch

        if not others:
            return []
        rendered = "\n".join(
            f"[{c.claim_id}] (owner {c.owner}, dim {c.dimension}, conf {c.confidence}, "
            f"v{c.version}) cites {c.evidence_ids}\n  {c.assertion}"
            for c in others
        )
        user = (
            f"QUESTION:\n{question}\n\n"
            "You are REVIEWING other specialists' claims (you do not own these). From "
            "your domain expertise, file evidence-backed objections on any claim you "
            "find weak. Each objection must EITHER cite an evidence id that contradicts "
            "the claim or that it omits, OR (for an inference_dispute) name the concrete "
            "flaw in its reasoning — never a bare 'I disagree'.\n\n"
            f"CLAIMS TO REVIEW:\n{rendered}\n\n"
            f"SHARED EVIDENCE (cite ids exactly as shown):\n{evidence_digest}\n\n"
            "Return only objections you can substantiate; an empty list is fine."
        )
        structured = self._model.with_structured_output(ChallengeBatch)
        cfg = {"callbacks": callbacks, "run_name": f"review:{self.key}"} if callbacks else {}
        try:
            batch = (self._template(user) | structured).invoke({}, config=cfg)
        except Exception:  # noqa: BLE001 - a critic failure just yields no objections
            return []
        return list(batch.challenges)

    def respond(
        self,
        claim,
        challenge,
        evidence_digest: str,
        valid_ids: set[str],
        callbacks: list | None = None,
    ):
        """Owner's single response to a challenge: cite / concede / revise."""
        from server.core.evidence.models import AuthorResponse

        user = (
            f"YOUR CLAIM (id {claim.claim_id}, v{claim.version}):\n{claim.assertion}\n"
            f"cited evidence: {claim.evidence_ids}\n\n"
            f"CHALLENGE ({challenge.kind}, severity {challenge.severity}):\n"
            f"{challenge.rationale}\n"
            f"challenge evidence: {challenge.evidence_ids}\n\n"
            f"SHARED EVIDENCE:\n{evidence_digest}\n\n"
            "Respond ONCE. If the challenge is right, either 'concede' or 'revise' "
            "(narrow/qualify the claim, citing evidence). If the claim still holds, "
            "'cite' stronger evidence. Stay within your domain and cite ids that exist."
        )
        structured = self._model.with_structured_output(AuthorResponse)
        cfg = {"callbacks": callbacks, "run_name": f"respond:{self.key}"} if callbacks else {}
        response = (self._template(user) | structured).invoke({}, config=cfg)
        response.evidence_ids = [e for e in response.evidence_ids if e in valid_ids]
        return response


def build_claim_writer(
    *,
    prompt_path: Path,
    dimension: str,
    key: str,
    label: str,
    model_name: str | None = None,
    temperature: float = 0.0,
) -> ClaimWriterAgent:
    """Build a v2 specialist claim-writer from a persona prompt."""
    from server.core.prompts import get_agent_prompt
    from server.core.agents.models import resolve_model, supports_temperature

    prompt = get_agent_prompt(prompt_path)
    resolved = resolve_model(model_name)
    kwargs = {"model": resolved}
    if supports_temperature(resolved):
        kwargs["temperature"] = temperature
    model = ChatOpenAI(**kwargs)
    return ClaimWriterAgent(
        model,
        prompt.text,
        dimension=dimension,
        key=key,
        label=label,
        prompt_client=prompt.client,
    )


def build_agent(
    *,
    prompt_path: Path,
    tools: list,
    model_name: str | None = None,
    temperature: float = 0.3,
):
    # Lazy import avoids a circular dependency (prompts.py imports load_prompt).
    from server.core.prompts import get_agent_prompt, register_agent_prompt
    from server.core.agents.models import resolve_model, supports_temperature

    prompt = get_agent_prompt(prompt_path)
    resolved = resolve_model(model_name)
    # Reasoning models (e.g. o4-mini) reject a custom temperature; omit it so they
    # fall back to their only supported value.
    kwargs = {"model": resolved, "streaming": True}
    if supports_temperature(resolved):
        kwargs["temperature"] = temperature
    model = ChatOpenAI(**kwargs)

    if tools:
        agent = create_react_agent(
            model, tools, prompt=prompt.text, checkpointer=MemorySaver()
        )
        # Persona linking for ReAct agents is done at invoke time (nodes pass
        # ``langfuse_prompt`` via config metadata); register the client so the
        # node can look it up.
        register_agent_prompt(agent, prompt.client)
        return agent
    return SimpleAgent(model, prompt.text, prompt_client=prompt.client)
