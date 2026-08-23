"""Run endpoints: create / list / get / status / delete.

The config-driven entrypoint. ``RunConfig`` is the request body ("the config
file" the wizard builds); pydantic rejects malformed configs with 422.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.core.config.schema import RunConfig
from server.core.orchestration.run_manager import run_manager

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("")
def create_run(config: RunConfig):
    try:
        return run_manager.create_run(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("")
def list_runs():
    """Card-feed data, newest first. This is the 3s poll target."""
    return run_manager.list_summaries()


@router.get("/{run_id}")
def get_run(run_id: str):
    record = run_manager.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record


@router.get("/{run_id}/status")
def run_status(run_id: str):
    status = run_manager.status(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return status


@router.delete("/{run_id}")
def delete_run(run_id: str):
    if not run_manager.delete_run(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"deleted": run_id}
