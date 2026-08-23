"""Langfuse-backed prompt management with local-file fallback + trace linking.

Prompts live in Langfuse under the ``production`` label so they can be versioned
and edited without a redeploy. Every prompt is ALSO kept in the repo — the
``prompt.md`` files and the constants in ``orchestration/prompts.py`` /
``follow_up.py`` — which serve two jobs: they seed the initial upload (see
``scripts/migrate_prompts.py``) and they are the runtime fallback. If Langfuse is
unconfigured or unreachable we read the local copy, so the app always boots.
This mirrors the defensive stance of ``observability.py``.

Two shapes:

``get_text(name, *, fallback)``
    Personas / static system prompts (no variables).

``get_compiled(name, *, fallback, **variables)``
    Templated prompts. Variables are ``{{double_brace}}`` in Langfuse and
    ``{single_brace}`` in the local fallback (plain ``str.format``).

Both return a :class:`Prompt` carrying the resolved string plus the originating
``LangfusePromptClient`` (``None`` on fallback). The client lets callers link the
prompt to their traces via ``metadata={"langfuse_prompt": client}`` — the
LangChain ``CallbackHandler`` registers that on the surrounding chain run and
links the generations it produces, so each trace shows which prompt version ran.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.core.orchestration.observability import _configured


@dataclass(frozen=True)
class Prompt:
    """A resolved prompt string plus its Langfuse client (if fetched remotely)."""

    text: str
    client: Any = None

    @property
    def link_metadata(self) -> dict:
        """``metadata`` fragment that links this prompt to a LangChain run."""
        return {"langfuse_prompt": self.client} if self.client is not None else {}


def _client():
    """The Langfuse client, or ``None`` when unconfigured/unavailable."""
    if not _configured():
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:  # noqa: BLE001 - never let prompt loading break a run
        return None


def get_text(name: str, *, fallback: str) -> Prompt:
    """Fetch a no-variable prompt by name; fall back to ``fallback`` text."""
    client = _client()
    if client is None:
        return Prompt(fallback)
    try:
        prompt = client.get_prompt(name, label="production")
        return Prompt(prompt.prompt, prompt)
    except Exception:  # noqa: BLE001 - missing prompt / network -> local copy
        return Prompt(fallback)


def get_compiled(name: str, *, fallback: str, **variables: Any) -> Prompt:
    """Fetch a templated prompt and compile ``variables`` into it.

    On fallback, the local ``fallback`` template uses single-brace ``{var}`` and
    is filled with ``str.format`` so behaviour matches the pre-migration code.
    """
    client = _client()
    if client is None:
        return Prompt(_local_format(fallback, variables))
    try:
        prompt = client.get_prompt(name, label="production")
        return Prompt(prompt.compile(**variables), prompt)
    except Exception:  # noqa: BLE001 - missing prompt / network -> local copy
        return Prompt(_local_format(fallback, variables))


def _local_format(template: str, variables: dict) -> str:
    try:
        return template.format(**variables)
    except (KeyError, IndexError, ValueError):
        # A malformed local template must never crash the run; ship it raw.
        return template


# ---- agent persona helpers -------------------------------------------------

# Maps a persona's .md path to its Langfuse prompt name. Specialist-team agents
# live at ``specialist_teams/<team>/agents/<key>/prompt.md`` and are named
# ``agent/<team>/<key>``.
def name_from_path(prompt_path: Path) -> str:
    parts = list(Path(prompt_path).resolve().parts)
    if "specialist_teams" in parts:
        i = parts.index("specialist_teams")
        team, key = parts[i + 1], parts[i + 3]  # parts[i+2] == "agents"
        return f"agent/{team}/{key}"
    # General agents (router/summarizer/judge) pass explicit names elsewhere;
    # this is a sane default for any other layout.
    return Path(prompt_path).parent.name


def get_agent_prompt(prompt_path: Path) -> Prompt:
    """Persona prompt for a workflow agent, keyed off its on-disk location."""
    # Lazy import: importing base at module load pulls in the agents package,
    # which imports back into this module -> circular import.
    from server.core.agents.base import load_prompt

    return get_text(name_from_path(prompt_path), fallback=load_prompt(prompt_path))


# Agents built by ``build_agent`` (and the follow-up chat graphs) are LangChain
# Runnables we don't own, so we can't always set attributes on them. Keep the
# agent -> prompt-client link in a weak map and read it back at invoke time.
_agent_prompts: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()


def register_agent_prompt(agent: Any, client: Any) -> None:
    if client is None:
        return
    try:
        _agent_prompts[agent] = client
    except TypeError:  # agent not weak-referenceable -> linking just no-ops
        pass


def agent_link_metadata(agent: Any) -> dict:
    """``metadata`` fragment linking an agent's persona prompt to its run."""
    client = _agent_prompts.get(agent)
    return {"langfuse_prompt": client} if client is not None else {}
