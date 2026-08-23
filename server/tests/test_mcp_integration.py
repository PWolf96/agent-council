"""Live MCP integration — opt-in against the running football-analytics server.

Skips cleanly when the server isn't reachable (see ``requires_mcp``). Proves the
real handshake works end to end: discover tools (``tools/list``) and call one
(``tools/call``) through the SDK-backed client.
"""

from __future__ import annotations

from server.mcp import mcp_client

from server.tests.conftest import requires_mcp

SERVER = "football-analytics"


@requires_mcp
def test_list_tools_discovers_catalog():
    names = {spec["name"] for spec in mcp_client.list_tools(SERVER, use_cache=False)}
    # The server exposes at least these three tools.
    assert {"get_catalog", "query_data", "search_entities"} <= names


@requires_mcp
def test_get_catalog_returns_datasets():
    out = mcp_client.call_tool(SERVER, "get_catalog", {})
    assert isinstance(out, dict)
    assert "error" not in out
    # Catalog advertises the server's datasets.
    datasets = out.get("datasets") or {}
    assert "squad_standard_stats" in datasets
