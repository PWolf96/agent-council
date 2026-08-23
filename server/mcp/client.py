"""MCP client — the one seam between a researcher's decision and the wire.

A researcher (L2) decides "call ``query_data`` with these args"; this client is
*how* that reaches a server. It is an **Adapter** over the official ``mcp`` SDK:
callers get a tiny synchronous, never-raising surface

    mcp_client.call_tool(server, tool, args) -> payload | {"error": ...}
    mcp_client.list_tools(server)            -> [{"name","description","args_schema"}]

and never see the SDK, async, sessions, or the MCP ``initialize`` handshake.

* **Transport is per server** (a Strategy chosen from :class:`MCPServerConfig`):
  ``streamable_http`` (default), ``sse``, or ``stdio``. The SDK performs the
  initialize/session handshake; we just open a session, call, and close.
* **Failures are gaps, never crashes** (mirrors the researcher contract): an
  unregistered/disabled server returns ``{"error": ..., "_placeholder": True}``;
  any transport/protocol error returns ``{"error": ...}``. The caller writes
  these as ``is_empty`` evidence the Sufficiency gate can see.
* **Sync over async:** the SDK is async, our pipeline is sync. All SDK work runs
  on one lazily-started background event loop; sync methods submit a coroutine
  and block on the result. Sessions are per-call today; the structure leaves a
  session pool as a drop-in later without touching callers.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from server.mcp.config import MCPServerConfig, config_for


class _Runner:
    """Owns a daemon asyncio loop so synchronous callers can await coroutines."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever, name="mcp-event-loop", daemon=True
                ).start()
                self._loop = loop
        return self._loop

    def run(self, coro, timeout: float) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        return future.result(timeout)


@asynccontextmanager
async def _session(cfg: MCPServerConfig):
    """Open an initialized :class:`ClientSession` for ``cfg``'s transport."""
    headers = dict(cfg.headers) or None
    if cfg.transport == "stdio":
        params = StdioServerParameters(command=cfg.command, args=list(cfg.args))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    elif cfg.transport == "sse":
        async with sse_client(cfg.url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:  # streamable_http (default) — yields a 3-tuple (read, write, get_sid)
        # An MCP-configured httpx client carries our auth/headers and the right
        # SSE read timeout; passing it in keeps full control for production.
        async with create_mcp_http_client(headers=headers) as http_client:
            async with streamable_http_client(cfg.url, http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


def _join_text(content: Any) -> str:
    parts = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _parse_multi_json(text: str) -> list | None:
    """Parse concatenated JSON values (JSONL / pretty-printed objects) into a list.

    A tool that returns many rows often emits them as back-to-back JSON objects
    rather than one JSON array, which :func:`json.loads` rejects as "Extra data".
    Decode them one at a time so a multi-row result becomes a real ``list`` (not
    an opaque string the digest can only show the first row of). Returns ``None``
    if the text isn't cleanly a sequence of JSON values.
    """
    decoder = json.JSONDecoder()
    objs: list = []
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break
        try:
            obj, idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            return None
        objs.append(obj)
    return objs or None


def _map_result(result: Any) -> Any:
    """Flatten an SDK ``CallToolResult`` into a plain payload.

    Prefer ``structuredContent``; else join text blocks, JSON-parsing when
    possible. A tool-reported error becomes an ``{"error": ...}`` gap.
    """
    if getattr(result, "isError", False):
        text = _join_text(getattr(result, "content", None))
        return {"error": f"tool error: {text}" if text else "tool error"}
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    text = _join_text(getattr(result, "content", None))
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        # A single JSON doc failed; many tools stream rows as concatenated JSON
        # objects. Recover those as a list so every row reaches the digest.
        return _parse_multi_json(text) or text


def _map_tool(tool: Any) -> dict:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "args_schema": tool.inputSchema or {},
    }


def _describe_exc(exc: BaseException) -> str:
    """Surface the root cause — anyio wraps transport failures in an ExceptionGroup."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def _unconfigured_gap(server: str, cfg: MCPServerConfig | None) -> dict:
    """A clean gap dict for a server that can't be reached (None or disabled)."""
    detail = (
        f"add a [servers.{server}] entry or set MCP_*_URL"
        if cfg is None
        else "no url / disabled"
    )
    return {
        "error": f"MCP server {server!r} is not configured ({detail})",
        "_placeholder": True,
    }


class MCPClient:
    """Synchronous, never-raising client over one or more MCP servers."""

    def __init__(self) -> None:
        self._runner = _Runner()
        self._tool_cache: dict[str, list[dict]] = {}

    def call_tool(self, server: str, tool: str, args: dict | None = None) -> Any:
        """Invoke ``tool`` on ``server``; return its payload (never raises).

        An unconfigured/disabled server or any transport failure comes back as an
        ``{"error": ...}`` dict so the researcher records it as a gap.
        """
        cfg = config_for(server)
        if cfg is None or not cfg.is_reachable:
            return _unconfigured_gap(server, cfg)
        try:
            return self._runner.run(
                _call_tool_async(cfg, tool, args or {}), cfg.timeout_s + 5
            )
        except Exception as exc:  # noqa: BLE001 - any failure is a gap, not a crash
            return {"error": f"MCP call {server}.{tool} failed: {_describe_exc(exc)}"}

    def list_tools(self, server: str, *, use_cache: bool = True) -> list[dict]:
        """Discover ``server``'s tools (``tools/list``); ``[]`` on any failure.

        Cached per server (a server's catalog is stable within a process run).
        Feeds the researcher's decision prompt so it sees the *live* catalog.
        """
        if use_cache and server in self._tool_cache:
            return self._tool_cache[server]
        cfg = config_for(server)
        if cfg is None or not cfg.is_reachable:
            return []
        try:
            specs = self._runner.run(_list_tools_async(cfg), cfg.timeout_s + 5)
        except Exception:  # noqa: BLE001 - discovery failure -> fall back to static specs
            return []
        self._tool_cache[server] = specs
        return specs

    def clear_cache(self) -> None:
        self._tool_cache.clear()


async def _call_tool_async(cfg: MCPServerConfig, tool: str, args: dict) -> Any:
    async with _session(cfg) as session:
        return _map_result(await session.call_tool(tool, args))


async def _list_tools_async(cfg: MCPServerConfig) -> list[dict]:
    async with _session(cfg) as session:
        result = await session.list_tools()
        return [_map_tool(t) for t in result.tools]


# Process-wide singleton (mirrors the tool registry / run_manager pattern).
mcp_client = MCPClient()
