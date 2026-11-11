"""FastAPI application factory and service lifecycle."""
from __future__ import annotations

import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent.orchestrator import Orchestrator
from agent.permissions import ApprovalBroker
from agent.services import Services
from agent.task_manager import TaskManager
from core.config import AppSettings, Paths
from core.events import EventBus
from core.perf import PerfMonitor
from core.secrets import SecretStore
from core.settings_store import SettingsStore
from integrations.gmail import GmailService
from providers.groq_client import GroqClient
from providers.manager import ProviderManager
from storage.database import Database
from tools import build_registry
from tools.browser import BrowserManager
from tools.terminal import ProcessManager

log = logging.getLogger(__name__)

# Electron passes the package.json version; the fallback is for standalone runs.
VERSION = os.environ.get("JARVIS_APP_VERSION", "0.1.0")
PUBLIC_PATHS = {"/health", "/api/gmail/oauth/callback"}


class TokenAuthMiddleware:
    """Every HTTP/WebSocket request must carry the per-launch token that
    Electron generated; blocks other local processes and web pages."""

    def __init__(self, app: ASGIApp, token: str | None, allowed_origins: list[str]) -> None:
        self.app = app
        self.token = token
        self.allowed_origins = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not self.token:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if scope["type"] == "http" and (path in PUBLIC_PATHS or scope.get("method") == "OPTIONS"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        supplied = headers.get("x-jarvis-token")
        if not supplied:
            from urllib.parse import parse_qs
            supplied = (parse_qs(scope.get("query_string", b"").decode()).get("token") or [None])[0]
        origin = headers.get("origin")
        origin_ok = origin is None or origin in self.allowed_origins
        if not supplied or not hmac.compare_digest(supplied, self.token) or not origin_ok:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401})
                return
            return await JSONResponse({"detail": "unauthorized"}, status_code=401)(scope, receive, send)
        return await self.app(scope, receive, send)


def create_app(*, paths: Paths | None = None, secrets: SecretStore | None = None,
               groq_transport: httpx.AsyncBaseTransport | None = None, auth_token: str | None = None,
               allowed_origins: list[str] | None = None, enable_voice: bool = True) -> FastAPI:
    token = auth_token if auth_token is not None else os.environ.get("JARVIS_AUTH_TOKEN")
    origins = allowed_origins if allowed_origins is not None else [
        o for o in os.environ.get("JARVIS_ALLOWED_ORIGINS", "app://jarvis,http://localhost:5173").split(",") if o]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        t0 = time.perf_counter()
        p = paths or Paths()
        p.ensure()
        db = Database(p.database)
        await db.open()
        store = SettingsStore(db)
        settings = await store.load()
        events = EventBus()
        sec = secrets or SecretStore()
        pv = settings.providers
        providers = ProviderManager(db, sec, events, threshold=pv.quota_threshold, timeout_s=pv.request_timeout_s,
                                    max_attempts=pv.max_attempts, transport=groq_transport)
        providers.configure(threshold=pv.quota_threshold, timeout_s=pv.request_timeout_s, max_attempts=pv.max_attempts,
                            local_limits=pv.local_limits, max_wait_s=pv.max_wait_s)
        await providers.load()
        approvals = ApprovalBroker(events)
        perf = PerfMonitor()
        services = Services(
            paths=p, db=db, settings_store=store, events=events, secrets=sec, providers=providers,
            groq=GroqClient(providers), approvals=approvals, perf=perf,
            browser=BrowserManager(p, lambda: store.current), processes=ProcessManager(), gmail=GmailService(db, sec))
        registry = build_registry()
        tasks = TaskManager(events, approvals, settings.automation.max_concurrent_tasks)
        orchestrator = Orchestrator(services, registry, tasks)
        if enable_voice:
            from voice.pipeline import VoiceService
            services.voice = VoiceService(services, orchestrator)

        async def on_settings(old: AppSettings, new: AppSettings) -> None:
            providers.configure(threshold=new.providers.quota_threshold, timeout_s=new.providers.request_timeout_s,
                                max_attempts=new.providers.max_attempts, local_limits=new.providers.local_limits,
                                max_wait_s=new.providers.max_wait_s)
            tasks.set_limit(new.automation.max_concurrent_tasks)
            if services.voice:
                await services.voice.apply_settings()
            if old.browser != new.browser and services.browser.running:
                await services.browser.close()
            await events.emit("settings.changed", settings=new.model_dump())
        store.on_change(on_settings)

        app.state.services = services
        app.state.registry = registry
        app.state.tasks = tasks
        app.state.orchestrator = orchestrator
        app.state.version = VERSION
        if services.voice:
            await services.voice.apply_settings()
        perf.startup_ms = round((time.perf_counter() - t0) * 1000, 1)
        log.info("backend ready in %.0f ms (data dir %s)", perf.startup_ms, p.data)
        try:
            yield
        finally:
            log.info("shutting down")
            await tasks.shutdown()
            if services.voice:
                await services.voice.close()
            await services.processes.stop_all()
            await services.browser.close()
            await services.gmail.close()
            await providers.close()
            await db.close()

    app = FastAPI(title="Jarvis backend", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TokenAuthMiddleware, token=token, allowed_origins=origins)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    from api.routes import router
    from api.ws import ws_router
    app.include_router(router)
    app.include_router(ws_router)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": VERSION, "ready": hasattr(app.state, "services")}

    return app
