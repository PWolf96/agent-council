"""MCP (Model Context Protocol) gateway — the agents' only door to tools.

Agents no longer carry in-code tools; every tool lives behind an MCP server.
This package is the seam: researchers (L2) decide *which* MCP tools to call;
:data:`mcp_client` is *how* the call reaches a server, and :mod:`config` is the
declarative, env-overridable registry of *which* servers exist and where.

Public surface:

- :data:`mcp_client` — process-wide client (``call_tool`` / ``list_tools``).
- :func:`config_for`, :func:`available_servers`, :class:`MCPServerConfig` — the
  server registry.
- :func:`is_empty_payload`, :func:`is_negative_result` — payload gap detection.
"""

from server.mcp.client import MCPClient, mcp_client
from server.mcp.config import (
    MCPServerConfig,
    available_servers,
    config_for,
    load_servers,
    reload,
)
from server.mcp.payloads import is_empty_payload, is_negative_result

__all__ = [
    "MCPClient",
    "mcp_client",
    "MCPServerConfig",
    "config_for",
    "available_servers",
    "load_servers",
    "reload",
    "is_empty_payload",
    "is_negative_result",
]
