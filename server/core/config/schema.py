"""RunConfig: the single source of truth for a run.

The UI wizard serializes one of these ("the config file") and POSTs it with the
prompt. The backend persists it verbatim and drives the deliberation from it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from server.core.agents.models import DEFAULT_MODEL, is_valid_model


class RunConfig(BaseModel):
    prompt: str
    team_id: str
    # Let the planner choose the participating specialists from the question.
    smart_routing: bool = False
    # Explicit specialist subset (by key). None/[] = all team specialists.
    # Ignored when smart_routing is on (the planner decides the set).
    agent_keys: list[str] | None = None
    # Model every specialist inherits unless overridden in ``agent_models``.
    default_model: str = DEFAULT_MODEL
    # Per-specialist model overrides keyed by agent key.
    agent_models: dict[str, str] = Field(default_factory=dict)
    # Deliberation loop bounds: hard cap on challenge passes and the per-pass
    # challenge budget. These keep worst-case cost predictable.
    max_passes: int = 3
    per_pass_budget: int = 4

    @field_validator("prompt")
    @classmethod
    def _prompt_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prompt must not be empty")
        return v.strip()

    @field_validator("default_model")
    @classmethod
    def _known_default_model(cls, v: str) -> str:
        if not is_valid_model(v):
            raise ValueError(f"unknown model: {v!r}")
        return v

    @field_validator("agent_models")
    @classmethod
    def _known_agent_models(cls, v: dict[str, str]) -> dict[str, str]:
        for key, model in v.items():
            if not is_valid_model(model):
                raise ValueError(f"unknown model for agent {key!r}: {model!r}")
        return v

    def model_post_init(self, _context) -> None:
        if self.max_passes < 1:
            raise ValueError("max_passes must be >= 1")
        if self.per_pass_budget < 1:
            raise ValueError("per_pass_budget must be >= 1")

    def model_for(self, agent_key: str) -> str:
        """Resolve the model a specialist runs on (override else default)."""
        return self.agent_models.get(agent_key, self.default_model)
