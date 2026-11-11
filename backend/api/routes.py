"""REST API used by the renderer (all routes require the launch token)."""
from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, ValidationError

from agent.permissions import LEVEL_NAMES
from agent.services import Services
from integrations.gmail import GmailError

router = APIRouter()


def S(request: Request) -> Services:  # noqa: N802
    return request.app.state.services


# ---------------------------------------------------------------- lifecycle
@router.post("/api/shutdown")
async def shutdown(request: Request) -> dict[str, bool]:
    server = getattr(request.app.state, "server", None)
    if server is not None:
        server.should_exit = True
    return {"ok": True}


@router.get("/api/state")
async def state(request: Request) -> dict[str, Any]:
    s = S(request)
    return {
        "version": request.app.state.version,
        "settings": s.settings.model_dump(),
        "voice": s.voice.status() if s.voice else None,
        "active_tasks": request.app.state.tasks.active(),
        "approvals": s.approvals.list(),
        "gmail": await s.gmail.status(),
        "providers_configured": len(s.providers.providers),
        "data_dir": str(s.paths.data),
        "permission_levels": LEVEL_NAMES,
    }


# ----------------------------------------------------------------- settings
@router.get("/api/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    return S(request).settings.model_dump()


@router.put("/api/settings")
async def put_settings(request: Request, patch: dict[str, Any]) -> dict[str, Any]:
    try:
        new = await S(request).settings_store.update(patch)
    except ValidationError as exc:
        raise HTTPException(422, detail=[{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()])
    return new.model_dump()


@router.post("/api/settings/reset")
async def reset_settings(request: Request) -> dict[str, Any]:
    s = S(request)
    await s.settings_store.reset()
    return (await s.settings_store.update({})).model_dump()


# ------------------------------------------------------------ conversations
@router.get("/api/conversations")
async def conversations(request: Request, q: Optional[str] = None, limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    return await request.app.state.orchestrator.conversations.list(limit, q)


@router.get("/api/conversations/{cid}/messages")
async def conversation_messages(request: Request, cid: str) -> list[dict[str, Any]]:
    return await request.app.state.orchestrator.conversations.messages_for_ui(cid)


class RenameIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


@router.patch("/api/conversations/{cid}")
async def rename_conversation(request: Request, cid: str, body: RenameIn) -> dict[str, bool]:
    await request.app.state.orchestrator.conversations.rename(cid, body.title)
    return {"ok": True}


@router.delete("/api/conversations/{cid}")
async def delete_conversation(request: Request, cid: str) -> dict[str, bool]:
    await request.app.state.orchestrator.conversations.delete(cid)
    return {"ok": True}


@router.delete("/api/conversations")
async def delete_all_conversations(request: Request) -> dict[str, bool]:
    await request.app.state.orchestrator.conversations.delete_all()
    return {"ok": True}


# -------------------------------------------------------------------- tasks
class SubmitIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    conversation_id: Optional[str] = Field(None, max_length=64)


@router.post("/api/tasks")
async def submit_task(request: Request, body: SubmitIn) -> dict[str, Any]:
    info = request.app.state.orchestrator.submit(body.text, body.conversation_id, "text")
    return {"task_id": info.id}


@router.get("/api/tasks")
async def task_history(request: Request, limit: int = Query(100, le=1000)) -> dict[str, Any]:
    rows = await S(request).db.fetchall("SELECT * FROM tasks ORDER BY started_at DESC LIMIT ?", (limit,))
    return {"active": request.app.state.tasks.active(), "history": rows}


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str) -> dict[str, bool]:
    return {"ok": request.app.state.tasks.cancel(task_id)}


@router.post("/api/tasks/cancel-all")
async def cancel_all(request: Request) -> dict[str, int]:
    s = S(request)
    if s.voice:
        await s.voice.cancel()
    await s.events.emit("tts.stop")
    return {"cancelled": request.app.state.tasks.cancel_all()}


class ApprovalIn(BaseModel):
    approved: bool


@router.post("/api/approvals/{approval_id}")
async def respond_approval(request: Request, approval_id: str, body: ApprovalIn) -> dict[str, bool]:
    return {"ok": S(request).approvals.respond(approval_id, body.approved)}


# --------------------------------------------------------------------- logs
@router.get("/api/logs")
async def execution_logs(request: Request, limit: int = Query(200, le=2000), task_id: Optional[str] = None) -> list[dict[str, Any]]:
    db = S(request).db
    if task_id:
        return await db.fetchall("SELECT * FROM execution_logs WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))
    return await db.fetchall("SELECT * FROM execution_logs ORDER BY id DESC LIMIT ?", (limit,))


@router.get("/api/logs/backend")
async def backend_log(request: Request, lines: int = Query(300, le=5000)) -> dict[str, Any]:
    path = S(request).paths.logs / "backend.log"

    def _tail() -> list[str]:
        if not path.exists():
            return []
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()[-lines:]
    return {"path": str(path), "lines": await asyncio.to_thread(_tail)}


@router.get("/api/tools")
async def tools(request: Request) -> list[dict[str, Any]]:
    return request.app.state.registry.catalog()


@router.get("/api/processes")
async def processes(request: Request) -> list[dict[str, Any]]:
    return S(request).processes.list()


@router.get("/api/perf")
async def perf(request: Request) -> dict[str, Any]:
    s = S(request)
    return s.perf.snapshot({
        "active_tasks": len(request.app.state.tasks.tasks), "ws_clients": s.events.client_count,
        "managed_processes": sum(1 for p in s.processes.processes.values() if p.proc.returncode is None),
        "browser_running": s.browser.running, "voice": s.voice.status() if s.voice else None,
    })


# ---------------------------------------------------------------- providers
class ProviderIn(BaseModel):
    label: str = Field("", max_length=60)
    api_key: str = Field(..., min_length=8, max_length=300)
    quota_scope: str = Field("default", max_length=60)


class ProviderPatch(BaseModel):
    label: Optional[str] = Field(None, max_length=60)
    enabled: Optional[bool] = None
    quota_scope: Optional[str] = Field(None, max_length=60)
    api_key: Optional[str] = Field(None, min_length=8, max_length=300)
    sort_order: Optional[int] = None


def _public(p: Any) -> dict[str, Any]:
    return {"id": p.id, "label": p.label, "enabled": p.enabled, "quota_scope": p.quota_scope, "key_hint": p.key_hint, "health": p.health}


@router.get("/api/providers")
async def providers_dashboard(request: Request, model: Optional[str] = None) -> dict[str, Any]:
    s = S(request)
    return await s.providers.dashboard(model or s.settings.providers.models.complex)


@router.post("/api/providers")
async def add_provider(request: Request, body: ProviderIn) -> dict[str, Any]:
    s = S(request)
    p = await s.providers.add(body.label, body.api_key, body.quota_scope)
    test = await s.providers.test(p.id)
    return {"provider": _public(p), "test": test}


@router.patch("/api/providers/{pid}")
async def patch_provider(request: Request, pid: str, body: ProviderPatch) -> dict[str, Any]:
    try:
        p = await S(request).providers.update(pid, **body.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, "provider not found")
    return _public(p)


@router.delete("/api/providers/{pid}")
async def delete_provider(request: Request, pid: str) -> dict[str, bool]:
    try:
        await S(request).providers.delete(pid)
    except KeyError:
        raise HTTPException(404, "provider not found")
    return {"ok": True}


@router.post("/api/providers/{pid}/test")
async def test_provider(request: Request, pid: str) -> dict[str, Any]:
    try:
        return await S(request).providers.test(pid)
    except KeyError:
        raise HTTPException(404, "provider not found")


@router.get("/api/models")
async def models(request: Request) -> dict[str, Any]:
    return {"models": await S(request).providers.available_models()}


# -------------------------------------------------------------------- voice
def _voice(request: Request) -> Any:
    v = S(request).voice
    if v is None:
        raise HTTPException(503, "voice is disabled")
    return v


class VoiceIn(BaseModel):
    conversation_id: Optional[str] = Field(None, max_length=64)


@router.get("/api/voice/status")
async def voice_status(request: Request) -> dict[str, Any]:
    return _voice(request).status()


@router.get("/api/voice/devices")
async def voice_devices(request: Request) -> dict[str, Any]:
    from voice.audio import MicrophoneError, list_input_devices
    try:
        return await asyncio.to_thread(list_input_devices)
    except MicrophoneError as exc:
        return {"devices": [], "default": None, "error": str(exc)}


@router.post("/api/voice/test-mic")
async def voice_test_mic(request: Request) -> dict[str, Any]:
    from voice.audio import check_microphone
    v = _voice(request)
    if v.state == "listening":
        return {"ok": True, "note": "microphone is in use by a voice session"}
    return await check_microphone(S(request).settings.voice.input_device)


@router.post("/api/voice/toggle")
async def voice_toggle(request: Request, body: VoiceIn | None = None) -> dict[str, str]:
    return {"state": await _voice(request).toggle(body.conversation_id if body else None)}


@router.post("/api/voice/start")
async def voice_start(request: Request, body: VoiceIn | None = None) -> dict[str, bool]:
    await _voice(request).start(body.conversation_id if body else None)
    return {"ok": True}


@router.post("/api/voice/stop")
async def voice_stop(request: Request) -> dict[str, bool]:
    await _voice(request).stop()
    return {"ok": True}


@router.post("/api/voice/cancel")
async def voice_cancel(request: Request) -> dict[str, bool]:
    await _voice(request).cancel()
    return {"ok": True}


class PauseIn(BaseModel):
    paused: bool


@router.post("/api/voice/pause")
async def voice_pause(request: Request, body: PauseIn) -> dict[str, bool]:
    await _voice(request).set_paused(body.paused)
    return {"paused": body.paused}


class SpeakIn(BaseModel):
    text: str = Field("Hello. Jarvis voice output is working.", max_length=1000)


@router.post("/api/voice/speak")
async def voice_speak(request: Request, body: SpeakIn) -> dict[str, bool]:
    await _voice(request).speak(body.text, task_id=None, kind="test")
    return {"ok": True}


@router.post("/api/tts/stop")
async def tts_stop(request: Request) -> dict[str, bool]:
    await S(request).events.emit("tts.stop")
    return {"ok": True}


@router.get("/api/tts/{clip_id}.wav")
async def tts_clip(request: Request, clip_id: str) -> Response:
    wav = _voice(request).tts.cache.get(clip_id)
    if wav is None:
        raise HTTPException(404, "clip expired")
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


# -------------------------------------------------------------------- gmail
class GmailClientIn(BaseModel):
    client_id: Optional[str] = Field(None, max_length=300)
    client_secret: Optional[str] = Field(None, max_length=300)
    client_json: Optional[str] = Field(None, max_length=10000, description="Contents of the downloaded client_secret_*.json")


@router.get("/api/gmail/status")
async def gmail_status(request: Request) -> dict[str, Any]:
    return await S(request).gmail.status()


@router.post("/api/gmail/client")
async def gmail_client(request: Request, body: GmailClientIn) -> dict[str, Any]:
    cid, secret = body.client_id, body.client_secret
    if body.client_json:
        try:
            data = json.loads(body.client_json)
            block = data.get("installed") or data.get("web") or {}
            cid, secret = block.get("client_id"), block.get("client_secret")
        except (ValueError, AttributeError):
            raise HTTPException(400, "Could not parse the client JSON file.")
    if not cid or not secret:
        raise HTTPException(400, "client_id and client_secret are required.")
    try:
        await S(request).gmail.configure(cid, secret)
    except GmailError as exc:
        raise HTTPException(400, str(exc))
    return await S(request).gmail.status()


@router.post("/api/gmail/connect")
async def gmail_connect(request: Request) -> dict[str, str]:
    port = request.url.port
    try:
        url = await S(request).gmail.start_auth(f"http://127.0.0.1:{port}/api/gmail/oauth/callback")
    except GmailError as exc:
        raise HTTPException(400, str(exc))
    return {"auth_url": url}


_PAGE = """<!doctype html><meta charset=utf-8><title>Jarvis</title>
<body style="font-family:Segoe UI,sans-serif;background:#111312;color:#e7ebe8;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h2 style="color:{color}">{title}</h2><p>{msg}</p><p style="opacity:.6">You can close this tab and return to Jarvis.</p></div>"""


@router.get("/api/gmail/oauth/callback", response_class=HTMLResponse)
async def gmail_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None) -> HTMLResponse:
    import html as _html
    s = S(request)
    if error or not code or not state:
        return HTMLResponse(_PAGE.format(color="#e0796b", title="Gmail not connected", msg=_html.escape(error or "Missing authorization code.")), 400)
    try:
        email = await s.gmail.complete_auth(code, state)
    except GmailError as exc:
        return HTMLResponse(_PAGE.format(color="#e0796b", title="Gmail not connected", msg=_html.escape(str(exc))), 400)
    await s.events.emit("gmail.changed")
    return HTMLResponse(_PAGE.format(color="#8fd19e", title="Gmail connected", msg=f"Signed in as {_html.escape(email)}."))


@router.post("/api/gmail/disconnect")
async def gmail_disconnect(request: Request) -> dict[str, Any]:
    s = S(request)
    await s.gmail.disconnect()
    await s.events.emit("gmail.changed")
    return await s.gmail.status()


@router.delete("/api/gmail/client")
async def gmail_remove(request: Request) -> dict[str, Any]:
    s = S(request)
    await s.gmail.remove_client()
    await s.events.emit("gmail.changed")
    return await s.gmail.status()


# --------------------------------------------------------------------- data
class ClearIn(BaseModel):
    conversations: bool = False
    task_history: bool = False
    usage: bool = False
    settings: bool = False
    browser_profile: bool = False


@router.post("/api/data/clear")
async def clear_data(request: Request, body: ClearIn) -> dict[str, Any]:
    s = S(request)
    cleared = []

    def _do(conn: Any) -> None:
        if body.conversations:
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM conversations")
            cleared.append("conversations")
        if body.task_history:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM execution_logs")
            cleared.append("task_history")
        if body.usage:
            conn.execute("DELETE FROM usage")
            conn.execute("DELETE FROM quota_state")
            cleared.append("usage")
    await s.db.transaction(_do)
    if body.usage:
        s.providers.quota.entries.clear()
        s.providers.quota._usage.clear()  # noqa: SLF001
    if body.settings:
        await s.settings_store.reset()
        await s.settings_store.update({})
        cleared.append("settings")
    if body.browser_profile:
        await s.browser.close()
        await asyncio.to_thread(shutil.rmtree, s.paths.browser_profile, True)
        cleared.append("browser_profile")
    if cleared:
        await s.db.execute("VACUUM")
    return {"cleared": cleared}
