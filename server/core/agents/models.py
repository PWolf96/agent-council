"""Catalog of LLM models agents can run on.

Single source of truth for both backend validation and the UI dropdown (served
via ``/api/models``). To expose a new model, add one entry to ``AVAILABLE_MODELS``
— nothing else needs to change.
"""

from __future__ import annotations

# Order matters: the first entry is the default the UI pre-selects and the
# fallback every agent inherits when no model is chosen.
AVAILABLE_MODELS: tuple[str, ...] = (
    "gpt-4o-mini",
    "o4-mini",
)

DEFAULT_MODEL: str = AVAILABLE_MODELS[0]

# Reasoning models that reject a custom ``temperature`` (only the default of 1 is
# allowed). Add a model here if the API rejects temperature for it.
_FIXED_TEMPERATURE_MODELS: frozenset[str] = frozenset({"o4-mini"})


def is_valid_model(name: str) -> bool:
    return name in AVAILABLE_MODELS


def supports_temperature(name: str) -> bool:
    return name not in _FIXED_TEMPERATURE_MODELS


def resolve_model(name: str | None) -> str:
    """Coerce an optional model name to a concrete, known model."""
    return name if name and is_valid_model(name) else DEFAULT_MODEL
