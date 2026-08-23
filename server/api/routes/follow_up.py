"""Follow-up conversation endpoints (still streamed via SSE).

Reuses the existing ``server.follow_up`` engine; the run record returned by the
RunManager keeps the legacy analysis shape it expects.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.core.orchestration.run_manager import run_manager
from server.follow_up import (
    create_conversation,
    delete_conversation,
    list_conversations,
    load_conversation,
    stream_follow_up,
)

router = APIRouter(prefix="/api/follow-up", tags=["follow-up"])


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class FollowUpMessageRequest(BaseModel):
    question: str


@router.post("/{debate_id}/conversations")
def create_conv(debate_id: str):
    if run_manager.get_run(debate_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return create_conversation(debate_id)


@router.get("/{debate_id}/conversations")
def list_convs(debate_id: str):
    return list_conversations(debate_id)


@router.get("/{debate_id}/conversations/{conv_id}")
def get_conv(debate_id: str, conv_id: str):
    conv = load_conversation(conv_id)
    if not conv or conv.get("debateId") != debate_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.delete("/{debate_id}/conversations/{conv_id}")
def del_conv(debate_id: str, conv_id: str):
    conv = load_conversation(conv_id)
    if not conv or conv.get("debateId") != debate_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    delete_conversation(conv_id)
    return {"deleted": conv_id}


@router.post("/{debate_id}/conversations/{conv_id}/message")
def send_message(debate_id: str, conv_id: str, req: FollowUpMessageRequest):
    record = run_manager.get_run(debate_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    conv = load_conversation(conv_id)
    if not conv or conv.get("debateId") != debate_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    def event_stream():
        try:
            for event in stream_follow_up(record, conv, req.question):
                yield _sse_event(event["event"], event["data"])
        except Exception as exc:  # noqa: BLE001
            yield _sse_event("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
