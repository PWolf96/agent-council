"""Model catalog for the wizard's model dropdowns."""

from __future__ import annotations

from fastapi import APIRouter

from server.core.agents.models import AVAILABLE_MODELS, DEFAULT_MODEL

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_models():
    return {
        "models": list(AVAILABLE_MODELS),
        "default": DEFAULT_MODEL,
    }
