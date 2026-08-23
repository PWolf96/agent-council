"""Module-level shared MemorySaver.

A single instance is reused across the deliberation graph (required for
Send-based parallel fan-out) and follow-up conversation agents (for
per-agent conversation memory). Keeping it module-level avoids
recreating the checkpointer on every graph compilation.

"""

from langgraph.checkpoint.memory import MemorySaver

shared_memory = MemorySaver()