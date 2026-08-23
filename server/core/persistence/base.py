"""Run registry + transcript persistence interface.

This is one of the two swap seams for a future durable backend (Redis/Postgres
queue): nothing above this imports a concrete store, only the protocol + the
``get_store()`` factory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class RunStore(ABC):
    @abstractmethod
    def create_run(self, record: dict) -> None:
        """Persist a new run record (must contain an ``id``)."""

    @abstractmethod
    def update_run(self, run_id: str, patch: dict) -> None:
        """Shallow-merge ``patch`` into an existing record and persist it."""

    @abstractmethod
    def get_run(self, run_id: str) -> dict | None:
        """Return the full run record, or None if it does not exist."""

    @abstractmethod
    def list_runs(self) -> list[dict]:
        """Return all run records, newest first."""

    @abstractmethod
    def delete_run(self, run_id: str) -> bool:
        """Delete a run. Return True if it existed."""
