"""Langfuse tracing seam (Langfuse Python SDK v4).

Wires LangChain/LangGraph runs into Langfuse so every agent invocation, tool
call, retrieved payload, and judge decision is captured as a nested trace. The
whole thing is opt-in and defensive: if the SDK isn't installed or the
``LANGFUSE_*`` credentials aren't set, the helpers here degrade to no-ops and
the deliberation runs exactly as before.

The integration follows the v4 LangChain guidance (the langfuse skill's
``references/instrumentation.md`` + https://langfuse.com/integrations/frameworks/langchain).
Two entry points cover the two execution shapes in this codebase:

``deliberation_trace()``
    A context manager for the single-threaded deliberation pipeline. It opens
    one named root span, sets the trace's input/output and session/tags via
    ``propagate_attributes``, and yields a handle carrying the callbacks plus a
    ``set_output()`` for the final decision. Everything run inside (planner,
    researchers, specialists as authors/critics/owners, synthesizer, evaluator)
    nests under the one trace because LangChain propagates callbacks down the run
    tree.

``langchain_config()``
    Builds a LangChain invoke config (callbacks + ``langfuse_*`` metadata). It
    is fully self-contained — no reliance on the ambient OTEL context — which
    makes it the right tool for the follow-up thread pool, where worker threads
    would not inherit a context-manager's span. Each call lands as its own
    trace, grouped with the original run via a shared ``session_id``.

Configure via the repo-root ``.env`` (also passed into the container by
docker-compose's ``env_file``):

    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_BASE_URL=https://cloud.langfuse.com   # EU cloud; US: https://us.cloud.langfuse.com; or self-hosted
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator


def _configured() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


@lru_cache(maxsize=1)
def _handler():
    """Build a single process-wide CallbackHandler, or ``None`` if unavailable.

    Cached because the handler is stateless w.r.t. a given run (per-run context
    is supplied by the enclosing span/attributes or the invoke metadata) and
    constructing it validates the client once.
    """
    if not _configured():
        return None
    try:
        from langfuse.langchain import CallbackHandler
    except Exception:  # noqa: BLE001 - missing/incompatible SDK must never break a run
        return None
    try:
        return CallbackHandler()
    except Exception:  # noqa: BLE001 - bad creds / network at init -> degrade silently
        return None


def flush() -> None:
    """Flush buffered observations. Safe to call when Langfuse isn't configured.

    The handler batches in the background; a freshly finished run on a
    short-lived worker would otherwise hold its trace until the next flush
    interval. Never raises.
    """
    if _handler() is None:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:  # noqa: BLE001 - flushing must never surface to the caller
        pass


class _NullTrace:
    """No-op handle yielded when Langfuse is not configured."""

    callbacks: list = []

    def set_output(self, output: Any) -> None:  # noqa: D401 - trivial no-op
        pass


class _Trace:
    """Handle for an active deliberation trace: callbacks + final output."""

    def __init__(self, span, callbacks: list):
        self.callbacks = callbacks
        self._span = span

    def set_output(self, output: Any) -> None:
        """Record the trace's headline output (e.g. the final verdict)."""
        try:
            self._span.update(output=output)
        except Exception:  # noqa: BLE001 - observability must never break a run
            pass


@contextmanager
def deliberation_trace(
    *, name: str, session_id: str, tags: list[str] | None = None, input: Any = None
) -> Iterator[_Trace | _NullTrace]:
    """Group a whole deliberation under one named Langfuse trace.

    Yields a handle whose ``.callbacks`` attach to the pipeline's LLM calls (and
    any planner call made inside the block). Attaching at the top is enough —
    LangChain propagates callbacks down the run tree, so the planner, researchers,
    specialists (critic + owner), and synthesizer are all traced under one root
    span. ``input``
    seeds the trace's input (the question); call ``handle.set_output(...)`` with
    the final decision before leaving the block.

    When Langfuse isn't configured this yields a no-op handle and does nothing
    else.
    """
    handler = _handler()
    if handler is None:
        yield _NullTrace()
        return

    from langfuse import get_client, propagate_attributes

    client = get_client()
    try:
        with client.start_as_current_observation(
            as_type="span", name=name, input=input
        ) as span:
            with propagate_attributes(
                trace_name=name, session_id=session_id, tags=tags or []
            ):
                yield _Trace(span, [handler])
    finally:
        flush()


def langchain_config(
    *,
    session_id: str,
    tags: list[str] | None = None,
    run_name: str | None = None,
    base: dict | None = None,
) -> dict:
    """Build a LangChain invoke config that routes the run into Langfuse.

    Everything needed is baked into the returned config (callbacks + the
    ``langfuse_*`` metadata keys the CallbackHandler reads), so this is safe
    across threads — unlike the context-manager approach, which relies on the
    ambient OTEL context that worker threads don't inherit. Use it for the
    follow-up thread pool. ``base`` lets callers merge in their existing config
    (e.g. ``{"configurable": {"thread_id": ...}}``).

    When Langfuse isn't configured, returns ``base`` unchanged (minus the
    Langfuse bits), so callers can use it unconditionally.
    """
    cfg = dict(base or {})
    handler = _handler()
    if handler is None:
        return cfg

    cfg["callbacks"] = [*cfg.get("callbacks", []), handler]
    metadata = dict(cfg.get("metadata") or {})
    metadata["langfuse_session_id"] = session_id
    if tags:
        metadata["langfuse_tags"] = tags
    cfg["metadata"] = metadata
    if run_name:
        cfg["run_name"] = run_name
    return cfg
