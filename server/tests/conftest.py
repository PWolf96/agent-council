"""Shared test fixtures for the v3 architecture suite.

Most tests are **deterministic and offline**: they exercise the evidence stores,
confidence rule, sufficiency/crux controllers, contradiction resolution, decision
aggregation, and the deliberation loop with *no* LLM and *no* MCP servers. A
handful of opt-in integration tests touch the live ``football-analytics`` MCP
server and the gpt-4o-mini model; they skip cleanly when that infra is absent.

Tools now live exclusively behind MCP servers (see ``server/mcp``): there is no
in-process database access here. When the MCP server is down, the pipeline
degrades gracefully — researcher calls record "not configured" gaps — so the
offline suite never needs a live server.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from pathlib import Path

from server.core.evidence.store import EvidenceContext  # noqa: E402

# server/tests/conftest.py -> parents[2] == repo root (where .env lives)
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


# --- infra availability gates -----------------------------------------------


def _mcp_available(server: str = "football-analytics") -> bool:
    """True only if the football-analytics MCP server answers tools/list."""
    try:
        from server.mcp import config_for, mcp_client

        cfg = config_for(server)
        if cfg is None or not cfg.is_reachable:
            return False
        return bool(mcp_client.list_tools(server, use_cache=False))
    except Exception:  # noqa: BLE001
        return False


def _openai_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


requires_mcp = pytest.mark.skipif(
    not _mcp_available(), reason="football-analytics MCP server not reachable"
)
requires_openai = pytest.mark.skipif(
    not _openai_available(), reason="OPENAI_API_KEY not set"
)

# Pin every LLM-touching test to the cheapest model.
TEST_MODEL = "gpt-4o-mini"


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def ctx() -> EvidenceContext:
    """A fresh, empty per-run evidence context."""
    return EvidenceContext("test-run")
