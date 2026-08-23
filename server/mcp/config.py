"""Declarative MCP server registry — config-as-data with env overrides.

The single source of truth for *which* MCP servers exist and *how* to reach
them. Defaults are checked in (``servers.toml``); every field is overridable per
server by environment variable, so promoting a server from local to production
is a **config/env change, not a code change** — the same researcher code talks
to ``http://localhost:3001/mcp`` in dev and a hardened prod URL (with a bearer
token) in production.

Env override convention. ``<KEY>`` is the server name upper-snaked, so
``football-analytics`` -> ``FOOTBALL_ANALYTICS``::

    MCP_<KEY>_URL          endpoint (streamable_http / sse)
    MCP_<KEY>_TRANSPORT    streamable_http | sse | stdio
    MCP_<KEY>_TOKEN        bearer token -> "Authorization: Bearer <token>"
    MCP_<KEY>_HEADERS      extra request headers, a JSON object
    MCP_<KEY>_ENABLED      "0"/"false"/"no" to disable (else enabled)
    MCP_<KEY>_COMMAND      stdio transport: executable to launch
    MCP_<KEY>_ARGS         stdio transport: JSON array of args

    MCP_CONFIG_FILE        path to an alternate servers.toml
    MCP_TIMEOUT_S          default per-call timeout (seconds) for all servers

``servers.toml`` is the registry of server *names*; env vars override the fields
of a named server. To add a brand-new server, add a one-line ``[servers.<name>]``
table (and point its URL at prod via env).
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal, Mapping

Transport = Literal["streamable_http", "sse", "stdio"]

_DEFAULT_TIMEOUT_S = 30.0
_FALSEY = {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class MCPServerConfig:
    """How to reach one MCP server. Immutable; built by :func:`load_servers`."""

    name: str
    transport: Transport = "streamable_http"
    url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout_s: float = _DEFAULT_TIMEOUT_S
    # stdio transport only:
    command: str = ""
    args: tuple[str, ...] = ()

    @property
    def is_reachable(self) -> bool:
        """Enabled and pointed at something — else calls become a clean gap."""
        if not self.enabled:
            return False
        if self.transport == "stdio":
            return bool(self.command)
        return bool(self.url)


def _env_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def _config_path() -> Path:
    override = os.getenv("MCP_CONFIG_FILE")
    return Path(override) if override else Path(__file__).parent / "servers.toml"


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in _FALSEY


def _apply_env(name: str, base: dict) -> dict:
    """Layer ``MCP_<KEY>_*`` env overrides onto a server's TOML defaults."""
    key = _env_key(name)
    out = dict(base)

    if (url := os.getenv(f"MCP_{key}_URL")) is not None:
        out["url"] = url
    if (transport := os.getenv(f"MCP_{key}_TRANSPORT")) is not None:
        out["transport"] = transport
    if (command := os.getenv(f"MCP_{key}_COMMAND")) is not None:
        out["command"] = command
    if (raw_args := os.getenv(f"MCP_{key}_ARGS")) is not None:
        try:
            out["args"] = tuple(json.loads(raw_args))
        except (json.JSONDecodeError, TypeError):
            pass

    headers = dict(out.get("headers") or {})
    if (raw_headers := os.getenv(f"MCP_{key}_HEADERS")) is not None:
        try:
            parsed = json.loads(raw_headers)
            if isinstance(parsed, dict):
                headers.update({str(k): str(v) for k, v in parsed.items()})
        except (json.JSONDecodeError, TypeError):
            pass
    if token := os.getenv(f"MCP_{key}_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    out["headers"] = headers

    out["enabled"] = _bool(os.getenv(f"MCP_{key}_ENABLED"), bool(out.get("enabled", True)))
    return out


def _build(name: str, table: dict) -> MCPServerConfig:
    merged = _apply_env(name, table)
    default_timeout = float(os.getenv("MCP_TIMEOUT_S", _DEFAULT_TIMEOUT_S))
    return MCPServerConfig(
        name=name,
        transport=merged.get("transport", "streamable_http"),
        url=merged.get("url", ""),
        headers=dict(merged.get("headers") or {}),
        enabled=bool(merged.get("enabled", True)),
        timeout_s=float(merged.get("timeout_s", default_timeout)),
        command=merged.get("command", ""),
        args=tuple(merged.get("args", ())),
    )


@lru_cache(maxsize=1)
def load_servers() -> dict[str, MCPServerConfig]:
    """All registered servers, ``name -> config`` (TOML defaults + env overrides).

    Cached; call :func:`reload` after mutating env (e.g. in tests) to rebuild.
    """
    path = _config_path()
    try:
        raw = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}
    servers = raw.get("servers", {}) if isinstance(raw, dict) else {}
    return {
        name: _build(name, table if isinstance(table, dict) else {})
        for name, table in servers.items()
    }


def reload() -> None:
    """Drop the cached registry so the next lookup re-reads file + env."""
    load_servers.cache_clear()


def config_for(name: str) -> MCPServerConfig | None:
    """Config for one server, or ``None`` if it isn't registered."""
    return load_servers().get(name)


def available_servers() -> list[str]:
    """Names of servers that are enabled and pointed at an endpoint."""
    return [name for name, cfg in load_servers().items() if cfg.is_reachable]
