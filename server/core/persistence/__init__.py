from pathlib import Path

from server.core.persistence.base import RunStore
from server.core.persistence.json_store import JsonFileStore

# server/core/persistence/__init__.py -> parents[2] == server/
SERVER_DIR = Path(__file__).resolve().parents[2]
RUNS_DIR = SERVER_DIR / "run_info" / "runs"

_store: RunStore | None = None


def get_store() -> RunStore:
    """Process-wide singleton run store."""
    global _store
    if _store is None:
        _store = JsonFileStore(RUNS_DIR)
    return _store


__all__ = ["RunStore", "JsonFileStore", "get_store", "RUNS_DIR"]
