"""WebSocket: server->renderer event stream plus a small set of validated
client commands."""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)
ws_router = APIRouter()


class ClientMessage(BaseModel):
    type: str = Field(..., max_length=40)
    text: str | None = Field(None, max_length=8000)
    conversation_id: str | None = Field(None, max_length=64)
    task_id: str | None = Field(None, max_length=64)
    id: str | None = Field(None, max_length=64)
    approved: bool | None = None
    playing: bool | None = None


@ws_router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await ws.accept()
    app = ws.app
    services = app.state.services
    events = services.events
    events.add(ws)
    await ws.send_json({"type": "hello", "version": app.state.version, "active_tasks": app.state.tasks.active(),
                        "approvals": services.approvals.list(),
                        "voice": services.voice.status() if services.voice else None})
    try:
        while True:
            raw = await ws.receive_json()
            try:
                msg = ClientMessage.model_validate(raw)
            except ValidationError:
                await ws.send_json({"type": "error", "error": "invalid message"})
                continue
            await _handle(ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("websocket error")
    finally:
        events.remove(ws)


async def _handle(ws: WebSocket, msg: ClientMessage) -> None:
    app = ws.app
    s = app.state.services
    t = msg.type
    if t == "ping":
        await ws.send_json({"type": "pong"})
    elif t == "chat.send" and msg.text:
        try:
            info = app.state.orchestrator.submit(msg.text, msg.conversation_id, "text")
        except ValueError as exc:
            await ws.send_json({"type": "error", "error": str(exc)})
            return
        await ws.send_json({"type": "chat.accepted", "task_id": info.id})
    elif t == "task.cancel" and msg.task_id:
        app.state.tasks.cancel(msg.task_id)
    elif t == "task.cancel_all":
        app.state.tasks.cancel_all()
        if s.voice:
            await s.voice.cancel()
        await s.events.emit("tts.stop")
    elif t == "approval.respond" and msg.id is not None and msg.approved is not None:
        s.approvals.respond(msg.id, msg.approved)
    elif t == "tts.state":
        await s.events.emit("tts.playing", playing=bool(msg.playing))
    elif t == "ui.conversation" and s.voice:
        s.voice.conversation_id = msg.conversation_id
    elif t == "voice.toggle" and s.voice:
        await s.voice.toggle(msg.conversation_id)
    elif t == "voice.start" and s.voice:
        await s.voice.start(msg.conversation_id)
    elif t == "voice.stop" and s.voice:
        await s.voice.stop()
    elif t == "voice.cancel" and s.voice:
        await s.voice.cancel()
    else:
        await ws.send_json({"type": "error", "error": f"unknown message type {t}"})
