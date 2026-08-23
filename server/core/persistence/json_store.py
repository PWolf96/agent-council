"""JSON-file implementation of RunStore (one file per run under server/run_info/runs/)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from server.core.persistence.base import RunStore


class JsonFileStore(RunStore):
    def __init__(self, runs_dir: Path):
        self._dir = Path(runs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # RLock: read-modify-write in update_run, and methods are called
        # concurrently from the executor's worker threads.
        self._lock = threading.RLock()

    def _path(self, run_id: str) -> Path:
        return self._dir / f"{run_id}.json"

    def _write(self, record: dict) -> None:
        path = self._path(record["id"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        tmp.replace(path)

    def create_run(self, record: dict) -> None:
        with self._lock:
            self._write(record)

    def update_run(self, run_id: str, patch: dict) -> None:
        with self._lock:
            current = self.get_run(run_id)
            if current is None:
                return
            current.update(patch)
            self._write(current)

    def get_run(self, run_id: str) -> dict | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        with self._lock:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None

    def list_runs(self) -> list[dict]:
        runs: list[dict] = []
        with self._lock:
            for path in self._dir.glob("*.json"):
                try:
                    runs.append(json.loads(path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        runs.sort(key=lambda r: r.get("created_at", 0), reverse=True)
        return runs

    def delete_run(self, run_id: str) -> bool:
        with self._lock:
            path = self._path(run_id)
            if not path.exists():
                return False
            path.unlink()
            return True
