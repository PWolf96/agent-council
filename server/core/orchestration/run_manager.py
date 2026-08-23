"""Run lifecycle: create -> queue -> run (background) -> complete/fail.

The frontend never streams; it polls. The manager owns status transitions and
persists the final transcript so the Deliberation/Stats views can render from
the record once a run completes.
"""

from __future__ import annotations

import time
import traceback
import uuid

from server.core.agents.teams import get_team
from server.core.config.schema import RunConfig
from server.core.orchestration.executor import InProcessExecutor, RunExecutor
from server.core.orchestration.registry import resolve_workflow
from server.core.orchestration.transcript import build_run_payload
from server.core.persistence import RunStore, get_store


class RunManager:
    def __init__(self, store: RunStore | None = None, executor: RunExecutor | None = None):
        self._store = store or get_store()
        self._executor = executor or InProcessExecutor()

    # ---- write paths -------------------------------------------------------

    def create_run(self, config: RunConfig) -> dict:
        # Validates the team exists (raises ValueError -> 400 at the API).
        team = get_team(config.team_id)
        run_id = str(uuid.uuid4())

        # The agents that will participate (best-effort for display; smart routing
        # may narrow this further at run time, which patches the agent list later).
        record = {
            "id": run_id,
            "status": "queued",
            "error": None,
            "created_at": time.time(),
            "completed_at": None,
            "prompt": config.prompt,
            "topic": config.prompt,  # kept for follow-up compatibility
            "teamId": config.team_id,
            "teamName": team.name,
            "smartRouting": config.smart_routing,
            "agentKeys": config.agent_keys,
            "agents": [
                {
                    "key": a.key,
                    "label": a.label,
                    "model": config.model_for(a.key),
                }
                for a in team.agents
            ],
            "config": {
                "defaultModel": config.default_model,
                "agentModels": config.agent_models,
                "maxPasses": config.max_passes,
                "perPassBudget": config.per_pass_budget,
                "smartRouting": config.smart_routing,
            },
            "routing": None,
            "insufficientAgents": None,
            "result": None,
        }
        self._store.create_run(record)
        self._executor.submit(run_id, lambda: self._execute(run_id, config))
        return {"run_id": run_id, "status": "queued"}

    def _execute(self, run_id: str, config: RunConfig) -> None:
        self._store.update_run(run_id, {"status": "running"})
        try:
            workflow = resolve_workflow(config.team_id, config)
            events = list(workflow.stream(config, run_id))
            payload = build_run_payload(events)

            patch = {
                "status": "completed",
                "completed_at": time.time(),
                "result": payload["result"],
                "routing": payload["routing"],
                "insufficientAgents": payload["insufficientAgents"],
            }
            routing = payload["routing"]
            if routing and routing.get("selected"):
                # Narrow the card's agent list to those that actually debated.
                patch["agents"] = routing["selected"]
            self._store.update_run(run_id, patch)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            traceback.print_exc()
            self._store.update_run(
                run_id,
                {"status": "failed", "error": str(exc), "completed_at": time.time()},
            )

    # ---- read paths --------------------------------------------------------

    def list_summaries(self) -> list[dict]:
        summaries = []
        for record in self._store.list_runs():
            result = record.get("result") or {}
            decision = result.get("decision") or {}
            summaries.append({
                "id": record["id"],
                "timestamp": record.get("created_at", 0),
                "prompt": record.get("prompt", ""),
                "teamId": record.get("teamId", ""),
                "teamName": record.get("teamName", ""),
                "smartRouting": record.get("smartRouting", False),
                "agents": record.get("agents", []),
                "status": record.get("status", "unknown"),
                "decided": bool(decision),
                "confidence": decision.get("confidence"),
                "error": record.get("error"),
            })
        return summaries

    def get_run(self, run_id: str) -> dict | None:
        return self._store.get_run(run_id)

    def status(self, run_id: str) -> dict | None:
        record = self._store.get_run(run_id)
        if record is None:
            return None
        result = record.get("result") or {}
        return {
            "id": record["id"],
            "status": record.get("status"),
            "decided": bool(result.get("decision")),
            "error": record.get("error"),
        }

    def delete_run(self, run_id: str) -> bool:
        return self._store.delete_run(run_id)


# Process-wide singleton used by the API layer.
run_manager = RunManager()
