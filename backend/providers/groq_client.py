"""High-level Groq calls (chat with tools, streaming, Whisper STT) routed
through the ProviderManager for key selection, failover and accounting."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

import httpx

from providers.manager import ProviderManager


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""


class GroqClient:
    def __init__(self, manager: ProviderManager) -> None:
        self.manager = manager

    async def chat(self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                   temperature: float = 0.3, max_tokens: int = 1024,
                   on_delta: Callable[[str], Any] | None = None, tool_choice: str = "auto",
                   reasoning_effort: str | None = "low") -> ChatResult:
        """Streamed chat completion. ``on_delta`` receives content tokens as they arrive."""
        await self.manager.ensure_models()
        model = self.manager.resolve_model(model, "chat")
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "temperature": temperature,
            "max_completion_tokens": max_tokens, "stream": True,
        }
        if reasoning_effort and model.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = reasoning_effort  # keeps hidden reasoning (and token use) short
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
            payload["parallel_tool_calls"] = True

        async def send(client: httpx.AsyncClient, key: str) -> httpx.Response:
            req = client.build_request("POST", "/chat/completions", json=payload,
                                       headers={"Authorization": f"Bearer {key}"})
            return await client.send(req, stream=True)

        started = time.perf_counter()
        provider, resp = await self.manager.request(model, "chat", send)
        result = ChatResult(model=model)
        calls: dict[int, dict[str, Any]] = {}
        try:
            async for chunk in _sse(resp):
                usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                if usage:
                    result.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    result.completion_tokens = int(usage.get("completion_tokens") or 0)
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if text := delta.get("content"):
                        result.content += text
                        if on_delta:
                            r = on_delta(text)
                            if hasattr(r, "__await__"):
                                await r
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(int(tc.get("index", len(calls))), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        result.finish_reason = choice["finish_reason"]
        finally:
            await resp.aclose()
        result.tool_calls = [calls[i] for i in sorted(calls)]
        result.latency_ms = (time.perf_counter() - started) * 1000
        await self.manager.record_success_usage(provider, model, "chat", prompt_tokens=result.prompt_tokens,
                                                completion_tokens=result.completion_tokens, latency_ms=result.latency_ms)
        return result

    async def transcribe(self, wav: bytes, *, model: str, language: str | None, duration_s: float) -> tuple[str, float]:
        """Groq Whisper transcription. Returns (text, latency_ms)."""
        await self.manager.ensure_models()
        model = self.manager.resolve_model(model, "stt")
        data = {"model": model, "response_format": "json", "temperature": "0"}
        if language:
            data["language"] = language

        async def send(client: httpx.AsyncClient, key: str) -> httpx.Response:
            return await client.post("/audio/transcriptions", data=data,
                                     files={"file": ("speech.wav", wav, "audio/wav")},
                                     headers={"Authorization": f"Bearer {key}"})

        started = time.perf_counter()
        provider, resp = await self.manager.request(model, "stt", send)
        latency = (time.perf_counter() - started) * 1000
        text = (resp.json().get("text") or "").strip()
        # Groq bills audio with a 10 s minimum per request; account for that.
        await self.manager.record_success_usage(provider, model, "stt", audio_seconds=max(10.0, duration_s), latency_ms=latency)
        return text, latency


async def _sse(resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in resp.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue
