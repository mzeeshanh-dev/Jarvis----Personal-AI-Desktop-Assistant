"""Shared fixtures. No live Groq credentials are needed: Groq is mocked with
httpx.MockTransport and secrets use an in-memory store."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.config import AppSettings, Paths  # noqa: E402
from core.events import EventBus  # noqa: E402
from core.secrets import MemorySecretStore  # noqa: E402
from storage.database import Database  # noqa: E402


def rl_headers(remaining_req: int = 14000, limit_req: int = 14400, remaining_tok: int = 5900, limit_tok: int = 6000,
               reset_req: str = "6m", reset_tok: str = "1s") -> dict[str, str]:
    return {
        "x-ratelimit-limit-requests": str(limit_req), "x-ratelimit-remaining-requests": str(remaining_req),
        "x-ratelimit-limit-tokens": str(limit_tok), "x-ratelimit-remaining-tokens": str(remaining_tok),
        "x-ratelimit-reset-requests": reset_req, "x-ratelimit-reset-tokens": reset_tok,
    }


def sse(*chunks: dict[str, Any]) -> bytes:
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def text_completion(text: str, usage: tuple[int, int] = (50, 10)) -> bytes:
    return sse({"choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]},
               {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "x_groq": {"usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}})


def tool_completion(calls: list[tuple[str, dict[str, Any]]]) -> bytes:
    deltas = [{"index": i, "id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
              for i, (n, a) in enumerate(calls)]
    return sse({"choices": [{"index": 0, "delta": {"tool_calls": deltas}}]},
               {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "x_groq": {"usage": {"prompt_tokens": 80, "completion_tokens": 20}}})


class GroqMock:
    """Scriptable fake Groq API. ``responses`` is a queue of callables or
    (status, headers, body) tuples consumed per request; models list is fixed."""

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.requests: list[httpx.Request] = []
        self.models = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "whisper-large-v3-turbo"]

    def push(self, status: int = 200, headers: dict[str, str] | None = None, body: bytes | dict | str = b"") -> None:
        self.queue.append((status, headers or rl_headers(), body))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            if request.headers.get("authorization") == "Bearer bad-key-000":
                return httpx.Response(401, json={"error": {"message": "invalid"}})
            return httpx.Response(200, json={"data": [{"id": m, "active": True} for m in self.models]})
        if not self.queue:
            return httpx.Response(500, text="no scripted response")
        item = self.queue.pop(0)
        if callable(item):
            return item(request)
        status, headers, body = item
        if isinstance(body, dict):
            return httpx.Response(status, headers=headers, json=body)
        if isinstance(body, str):
            body = body.encode()
        return httpx.Response(status, headers={"content-type": "text/event-stream", **headers}, content=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def keys_used(self) -> list[str]:
        return [r.headers["authorization"].removeprefix("Bearer ") for r in self.requests if not r.url.path.endswith("/models")]


@pytest.fixture
def groq() -> GroqMock:
    return GroqMock()


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    p = Paths(data_dir=tmp_path / "data", resources_dir=ROOT / "resources")
    p.ensure()
    return p


@pytest.fixture
async def db(paths: Paths):
    d = Database(paths.database)
    await d.open()
    yield d
    await d.close()


@pytest.fixture
def secrets() -> MemorySecretStore:
    return MemorySecretStore()


@pytest.fixture
def events() -> EventBus:
    return EventBus()


@pytest.fixture
def workspace():
    # pytest's tmp_path is under %LOCALAPPDATA%, which the path guard protects,
    # so test workspaces live inside the repo (git-ignored).
    import shutil
    import uuid
    w = ROOT / "tests" / ".work" / uuid.uuid4().hex[:8]
    w.mkdir(parents=True)
    yield w
    shutil.rmtree(w, ignore_errors=True)


@pytest.fixture
def settings(workspace: Path) -> AppSettings:
    s = AppSettings()
    s.permissions.workspace_roots = [str(workspace)]
    return s


class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    import time as _time
    c = FakeClock()
    monkeypatch.setattr(_time, "time", c)
    return c


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def make_manager(db, secrets, events, groq) -> Callable[..., Any]:
    from providers.manager import ProviderManager

    def factory(**kw: Any) -> ProviderManager:
        return ProviderManager(db, secrets, events, transport=groq.transport, sleep=no_sleep, **kw)
    return factory
