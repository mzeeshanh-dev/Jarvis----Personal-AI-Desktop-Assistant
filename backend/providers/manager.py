"""Groq multi-credential manager: fair round-robin over *eligible* keys.

Eligibility (checked in order for every request):
1. enabled  2. credential present  3. credential not marked invalid (401)
4. circuit breaker closed / half-open probe available  5. quota scope + model
not frozen (threshold, 429, local estimate)  6. model not known-unsupported.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from core.events import EventBus
from core.secrets import SecretStore, key_hint, redact
from providers.quota import LocalLimitSpec, QuotaTracker
from storage.database import Database

log = logging.getLogger(__name__)

GROQ_BASE = "https://api.groq.com/openai/v1"


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_at: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_at = retry_at


class NoProviderAvailable(ProviderError):
    def __init__(self, message: str, reasons: dict[str, str], retry_at: float | None) -> None:
        super().__init__(message, None, retry_at)
        self.reasons = reasons

    def user_message(self) -> str:
        if not self.reasons:
            return "No Groq API key is configured. Add one in Settings → Groq API keys."
        msg = "All Groq API keys are currently unavailable"
        if self.retry_at:
            secs = max(0, int(self.retry_at - time.time()))
            msg += f"; expected to recover in about {secs // 60}m {secs % 60}s"
        return msg + "."


@dataclass
class Breaker:
    threshold: int = 3
    failures: int = 0
    open_until: float = 0.0
    probe_inflight: bool = False

    def allow(self, now: float) -> bool:
        if self.failures < self.threshold:
            return True
        if self.open_until > now:
            return False
        return not self.probe_inflight  # half-open

    def state(self, now: float) -> str:
        if self.failures < self.threshold:
            return "closed"
        return "open" if self.open_until > now else "half-open"

    def on_attempt(self, now: float) -> None:
        if self.failures >= self.threshold and self.open_until <= now:
            self.probe_inflight = True

    def on_success(self) -> None:
        self.failures, self.open_until, self.probe_inflight = 0, 0.0, False

    def on_failure(self, now: float) -> None:
        self.failures += 1
        self.probe_inflight = False
        if self.failures >= self.threshold:
            self.open_until = now + min(300.0, 5.0 * 2 ** (self.failures - self.threshold))


@dataclass
class Provider:
    id: str
    label: str
    enabled: bool
    quota_scope: str
    credential_ref: str
    key_hint: str
    sort_order: int
    health: str = "unknown"  # unknown|healthy|degraded|invalid
    last_success_at: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    models: set[str] | None = None
    models_fetched_at: float | None = None
    forbidden_models: dict[str, float] = field(default_factory=dict)
    breaker: Breaker = field(default_factory=Breaker)
    has_secret: bool = True


Sender = Callable[[httpx.AsyncClient, str], Awaitable[httpx.Response]]


class ProviderManager:
    def __init__(self, db: Database, secrets: SecretStore, events: EventBus, *,
                 threshold: float = 0.97, timeout_s: float = 60.0, max_attempts: int = 4,
                 base_url: str = GROQ_BASE, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.db, self.secrets, self.events = db, secrets, events
        self.base_url = base_url
        self.max_attempts = max_attempts
        self.max_wait_s = 20.0
        self.quota = QuotaTracker(threshold)
        self.providers: dict[str, Provider] = {}
        self._order: list[str] = []
        self._cursor = 0
        self._lock = asyncio.Lock()
        self._sleep = sleep
        self.active_provider_id: str | None = None
        self._fallback_logged: dict[str, str] = {}
        self._models_attempt_at = 0.0
        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout_s, connect=10.0), transport=transport)

    # ------------------------------------------------------------- lifecycle
    async def load(self) -> None:
        rows = await self.db.fetchall("SELECT * FROM providers WHERE kind='groq' ORDER BY sort_order, created_at")
        for r in rows:
            p = Provider(r["id"], r["label"], bool(r["enabled"]), r["quota_scope"], r["credential_ref"],
                         r["key_hint"] or "", r["sort_order"], r["health"], r["last_success_at"], r["last_error"], r["last_error_at"])
            p.has_secret = (await self.secrets.get(p.credential_ref)) is not None
            self.providers[p.id] = p
        self._reorder()
        self.quota.load(await self.db.fetchall("SELECT * FROM quota_state"))
        self.quota.load_usage(await self.db.fetchall(
            "SELECT scope, model, prompt_tokens, completion_tokens, audio_seconds, created_at FROM usage "
            "WHERE created_at > ? AND status_code BETWEEN 200 AND 299 ORDER BY created_at", (time.time() - 86400,)))

    async def close(self) -> None:
        await self.client.aclose()

    def configure(self, *, threshold: float, timeout_s: float, max_attempts: int, local_limits: dict[str, Any],
                  max_wait_s: float = 20.0) -> None:
        self.quota.threshold = threshold
        self.max_attempts = max_attempts
        self.max_wait_s = max_wait_s
        self.client.timeout = httpx.Timeout(timeout_s, connect=10.0)
        self.quota.local_limits = {m: LocalLimitSpec(**(v if isinstance(v, dict) else v.model_dump())) for m, v in local_limits.items()}

    def _reorder(self) -> None:
        self._order = [p.id for p in sorted(self.providers.values(), key=lambda p: (p.sort_order, p.id))]

    # ---------------------------------------------------------- credentials
    async def add(self, label: str, api_key: str, quota_scope: str = "default") -> Provider:
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("API key is empty")
        pid = uuid.uuid4().hex[:12]
        ref = f"groq:{pid}"
        await self.secrets.set(ref, api_key)
        order = max((p.sort_order for p in self.providers.values()), default=-1) + 1
        p = Provider(pid, label.strip() or f"Groq key {order + 1}", True, quota_scope.strip() or "default", ref, key_hint(api_key), order)
        await self.db.execute(
            "INSERT INTO providers(id, kind, label, enabled, quota_scope, credential_ref, key_hint, sort_order, health, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, "groq", p.label, 1, p.quota_scope, ref, p.key_hint, order, p.health, time.time()))
        self.providers[pid] = p
        self._reorder()
        await self._changed()
        return p

    async def update(self, pid: str, *, label: str | None = None, enabled: bool | None = None,
                     quota_scope: str | None = None, api_key: str | None = None, sort_order: int | None = None) -> Provider:
        p = self._get(pid)
        if label is not None:
            p.label = label.strip() or p.label
        if enabled is not None:
            p.enabled = enabled
        if quota_scope is not None:
            p.quota_scope = quota_scope.strip() or "default"
        if sort_order is not None:
            p.sort_order = sort_order
        if api_key:
            await self.secrets.set(p.credential_ref, api_key.strip())
            p.key_hint, p.health, p.has_secret = key_hint(api_key.strip()), "unknown", True
            p.breaker = Breaker()
            p.models = None
        await self.db.execute(
            "UPDATE providers SET label=?, enabled=?, quota_scope=?, key_hint=?, sort_order=?, health=? WHERE id=?",
            (p.label, int(p.enabled), p.quota_scope, p.key_hint, p.sort_order, p.health, pid))
        self._reorder()
        await self._changed()
        return p

    async def delete(self, pid: str) -> None:
        p = self._get(pid)
        await self.secrets.delete(p.credential_ref)
        await self.db.execute("DELETE FROM providers WHERE id=?", (pid,))
        del self.providers[pid]
        self._reorder()
        await self._changed()

    def _get(self, pid: str) -> Provider:
        if pid not in self.providers:
            raise KeyError(pid)
        return self.providers[pid]

    async def test(self, pid: str) -> dict[str, Any]:
        """Validate a credential with GET /models (also refreshes model list)."""
        p = self._get(pid)
        key = await self.secrets.get(p.credential_ref)
        if not key:
            p.has_secret = False
            return {"ok": False, "error": "Credential missing from Windows Credential Manager"}
        started = time.perf_counter()
        try:
            resp = await self.client.get("/models", headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            await self._mark_error(p, f"network error: {type(exc).__name__}")
            return {"ok": False, "error": "Network error while contacting Groq"}
        latency = (time.perf_counter() - started) * 1000
        if resp.status_code == 200:
            p.models = {m["id"] for m in resp.json().get("data", []) if m.get("active", True)}
            p.models_fetched_at = time.time()
            p.breaker.on_success()
            await self._mark_success(p)
            return {"ok": True, "latency_ms": round(latency), "models": sorted(p.models)}
        if resp.status_code == 401:
            await self._mark_invalid(p, "401 Unauthorized: invalid API key")
            return {"ok": False, "error": "Invalid API key (401)"}
        await self._mark_error(p, f"HTTP {resp.status_code}")
        return {"ok": False, "error": f"Groq returned HTTP {resp.status_code}"}

    async def available_models(self) -> list[str]:
        """Models reported by the provider (refreshed at most every 6h)."""
        for p in self.providers.values():
            if p.enabled and p.health != "invalid" and p.has_secret:
                if p.models is None or (time.time() - (p.models_fetched_at or 0)) > 6 * 3600:
                    await self.test(p.id)
        models: set[str] = set()
        for p in self.providers.values():
            models |= p.models or set()
        return sorted(models)

    # Fallback preference when a configured model is not offered by the provider.
    CHAT_FALLBACKS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant")
    STT_FALLBACKS = ("whisper-large-v3-turbo", "whisper-large-v3")
    _NON_CHAT = ("whisper", "guard", "orpheus", "tts", "playai")

    async def ensure_models(self) -> None:
        """Fetch the model list once (per 6 h) if no enabled key has one yet."""
        live = [p for p in self.providers.values() if p.enabled and p.has_secret and p.health != "invalid"]
        if live and all(p.models is None for p in live):
            now = time.time()
            if now - self._models_attempt_at > 300:
                self._models_attempt_at = now
                try:
                    await self.test(live[0].id)
                except Exception:  # noqa: BLE001 - model discovery is best effort
                    log.debug("model discovery failed", exc_info=True)

    def resolve_model(self, preferred: str, kind: str = "chat") -> str:
        """Return `preferred` if any key offers it (or model lists are unknown),
        otherwise the best available substitute."""
        known = [p.models for p in self.providers.values() if p.enabled and p.models is not None]
        if not known:
            return preferred
        offered: set[str] = set().union(*known)
        if preferred in offered:
            return preferred
        if kind == "stt":
            candidates = [m for m in self.STT_FALLBACKS if m in offered] or sorted(m for m in offered if "whisper" in m)
        else:
            candidates = [m for m in self.CHAT_FALLBACKS if m in offered] or sorted(
                m for m in offered if not any(x in m for x in self._NON_CHAT))
        if not candidates:
            return preferred
        if self._fallback_logged.get(preferred) != candidates[0]:
            log.warning("model %s is not available for the configured keys; using %s", preferred, candidates[0])
            self._fallback_logged[preferred] = candidates[0]
        return candidates[0]

    # ------------------------------------------------------------ selection
    def _ineligible_reason(self, p: Provider, model: str, now: float) -> tuple[str | None, float | None]:
        if not p.enabled:
            return "disabled", None
        if not p.has_secret:
            return "credential missing", None
        if p.health == "invalid":
            return "invalid credential", None
        if not p.breaker.allow(now):
            return "circuit open", p.breaker.open_until
        f = self.quota.check(p.quota_scope, model, now)
        if f.frozen:
            return f"quota scope '{p.quota_scope}' frozen ({f.metric})", f.until
        if p.models is not None and model not in p.models:
            return "model not available for this key", None
        until = p.forbidden_models.get(model)
        if until and until > now:
            return "model forbidden (403)", until
        return None, None

    def select(self, model: str, now: float | None = None) -> Provider:
        now = now or time.time()
        n = len(self._order)
        reasons: dict[str, str] = {}
        retry_times: list[float] = []
        for i in range(n):
            idx = (self._cursor + i) % n
            p = self.providers[self._order[idx]]
            reason, until = self._ineligible_reason(p, model, now)
            if reason is None:
                self._cursor = idx + 1
                return p
            reasons[p.label] = reason
            if until:
                retry_times.append(until)
        raise NoProviderAvailable("no eligible Groq provider", reasons, min(retry_times) if retry_times else None)

    # -------------------------------------------------------------- request
    async def request(self, model: str, kind: str, send: Sender) -> tuple[Provider, httpx.Response]:
        """Send through the next eligible key, failing over as needed.

        ``send(client, api_key)`` must return an httpx.Response; for streaming
        requests return it unread (``stream=True``). On success the caller owns
        the response and must call :meth:`record_success_usage` when done.
        """
        last_error: ProviderError | None = None
        waits = 0
        attempt = -1
        while attempt < self.max_attempts - 1:
            attempt += 1
            async with self._lock:
                try:
                    p = self.select(model)
                except NoProviderAvailable as exc:
                    if exc.retry_at and exc.retry_at - time.time() <= self.max_wait_s and waits < 3:
                        wait = max(0.05, exc.retry_at - time.time()) + 0.25
                        waits += 1
                        await self.events.emit("providers.waiting", seconds=round(wait, 1), reasons=exc.reasons)
                    else:
                        raise
                    p = None  # type: ignore[assignment]
                if p is not None:
                    now = time.time()
                    p.breaker.on_attempt(now)
                    self.quota.begin_probe(p.quota_scope, model)
            if p is None:
                await self._sleep(wait)
                attempt -= 1  # waiting for a quota window is not a failed attempt
                continue

            key = await self.secrets.get(p.credential_ref)
            if not key:
                p.has_secret = False
                self.quota.end_probe(p.quota_scope, model)
                continue
            self.active_provider_id = p.id
            started = time.perf_counter()
            try:
                resp = await send(self.client, key)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                latency = (time.perf_counter() - started) * 1000
                self.quota.end_probe(p.quota_scope, model)
                p.breaker.on_failure(time.time())
                await self._mark_error(p, f"{type(exc).__name__}")
                await self._log_usage(p, model, kind, None, latency, error=type(exc).__name__)
                last_error = ProviderError(f"Groq request failed: {type(exc).__name__}")
                await self._sleep(self._backoff(attempt))
                continue

            latency = (time.perf_counter() - started) * 1000
            status = resp.status_code
            if 200 <= status < 300:
                self.quota.update_from_headers(p.quota_scope, model, resp.headers)
                self.quota.end_probe(p.quota_scope, model)
                p.breaker.on_success()
                await self._mark_success(p)
                await self._persist_quota()
                resp.extensions["jarvis_latency_ms"] = latency
                return p, resp

            body = await self._read_error(resp)
            self.quota.end_probe(p.quota_scope, model)
            await self._log_usage(p, model, kind, status, latency, error=body[:300])
            if status == 429:
                until = self.quota.on_rate_limited(p.quota_scope, model, resp.headers)
                await self._mark_error(p, f"429 rate limited until {time.strftime('%H:%M:%S', time.localtime(until))}", health=p.health)
                await self._persist_quota()
                last_error = ProviderError("Rate limited by Groq", 429, until)
                continue  # next key; same-scope keys are now frozen too
            if status == 401:
                await self._mark_invalid(p, "401 Unauthorized: invalid API key")
                last_error = ProviderError("Invalid Groq API key", 401)
                continue
            if status == 403:
                p.forbidden_models[model] = time.time() + 600
                await self._mark_error(p, f"403 Forbidden for {model}: {body[:120]}")
                last_error = ProviderError("Groq refused access (403)", 403)
                continue
            if status == 404 and "model" in body.lower():
                if p.models is not None:
                    p.models.discard(model)
                else:
                    p.forbidden_models[model] = time.time() + 3600
                last_error = ProviderError(f"Model {model} is not available", 404)
                continue
            if status >= 500:
                p.breaker.on_failure(time.time())
                await self._mark_error(p, f"HTTP {status}")
                last_error = ProviderError(f"Groq server error {status}", status)
                await self._sleep(self._backoff(attempt))
                continue
            # 400 and other client errors are our fault: do not retry.
            raise ProviderError(f"Groq rejected the request ({status}): {redact(body[:300])}", status)
        raise last_error or ProviderError("Groq request failed")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.5 * 2 ** attempt) * (0.5 + random.random() / 2)

    @staticmethod
    async def _read_error(resp: httpx.Response) -> str:
        try:
            await resp.aread()
            return resp.text
        except Exception:  # noqa: BLE001
            return ""
        finally:
            await resp.aclose()

    async def record_success_usage(self, p: Provider, model: str, kind: str, *, prompt_tokens: int = 0,
                                   completion_tokens: int = 0, audio_seconds: float = 0.0, latency_ms: float | None = None) -> None:
        self.quota.record_usage(p.quota_scope, model, prompt_tokens + completion_tokens, audio_seconds)
        await self._log_usage(p, model, kind, 200, latency_ms, prompt_tokens=prompt_tokens,
                              completion_tokens=completion_tokens, audio_seconds=audio_seconds)
        await self._persist_quota()
        await self._changed()

    # ---------------------------------------------------------- bookkeeping
    async def _log_usage(self, p: Provider, model: str, kind: str, status: int | None, latency: float | None, *,
                         error: str | None = None, prompt_tokens: int = 0, completion_tokens: int = 0, audio_seconds: float = 0.0) -> None:
        await self.db.execute(
            "INSERT INTO usage(provider_id, scope, model, kind, status_code, prompt_tokens, completion_tokens, audio_seconds, latency_ms, error, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (p.id, p.quota_scope, model, kind, status, prompt_tokens, completion_tokens, audio_seconds, latency,
             redact(error) if error else None, time.time()))

    async def _mark_success(self, p: Provider) -> None:
        p.health, p.last_success_at = "healthy", time.time()
        await self.db.execute("UPDATE providers SET health=?, last_success_at=? WHERE id=?", (p.health, p.last_success_at, p.id))

    async def _mark_error(self, p: Provider, error: str, health: str | None = None) -> None:
        p.last_error, p.last_error_at = redact(error), time.time()
        p.health = health or ("degraded" if p.health != "invalid" else "invalid")
        await self.db.execute("UPDATE providers SET health=?, last_error=?, last_error_at=? WHERE id=?",
                              (p.health, p.last_error, p.last_error_at, p.id))
        await self._changed()

    async def _mark_invalid(self, p: Provider, error: str) -> None:
        await self._mark_error(p, error, health="invalid")

    async def _persist_quota(self) -> None:
        for e in self.quota.take_dirty():
            await self.db.execute(
                "INSERT INTO quota_state(scope, model, metric, limit_value, remaining, reset_at, frozen_until, source, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(scope, model, metric) DO UPDATE SET "
                "limit_value=excluded.limit_value, remaining=excluded.remaining, reset_at=excluded.reset_at, "
                "frozen_until=excluded.frozen_until, source=excluded.source, updated_at=excluded.updated_at",
                (e.scope, e.model, e.metric, e.limit, e.remaining, e.reset_at, e.frozen_until, e.source, e.updated_at))

    async def _changed(self) -> None:
        await self.events.emit("providers.changed")

    # ------------------------------------------------------------ dashboard
    async def dashboard(self, model: str | None = None) -> dict[str, Any]:
        now = time.time()
        day = now - 86400
        stats = {r["provider_id"]: r for r in await self.db.fetchall(
            "SELECT provider_id, COUNT(*) AS requests, SUM(CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END) AS ok, "
            "SUM(prompt_tokens + completion_tokens) AS tokens, SUM(audio_seconds) AS audio_s "
            "FROM usage WHERE created_at > ? GROUP BY provider_id", (day,))}
        errors = await self.db.fetchall(
            "SELECT u.provider_id, u.model, u.status_code, u.error, u.created_at FROM usage u "
            "WHERE u.error IS NOT NULL ORDER BY u.created_at DESC LIMIT 50")
        providers = []
        for pid in self._order:
            p = self.providers[pid]
            s = stats.get(pid, {})
            reason, until = self._ineligible_reason(p, model, now) if model else (None, None)
            providers.append({
                "available": reason is None, "unavailable_reason": reason,
                "available_in_s": max(0.0, until - now) if until else None,
                "id": p.id, "label": p.label, "enabled": p.enabled, "quota_scope": p.quota_scope,
                "key_hint": p.key_hint, "health": p.health, "has_secret": p.has_secret,
                "circuit": p.breaker.state(now), "circuit_open_for_s": max(0.0, p.breaker.open_until - now),
                "last_success_at": p.last_success_at, "last_error": p.last_error, "last_error_at": p.last_error_at,
                "models": sorted(p.models) if p.models is not None else None,
                "requests_24h": s.get("requests") or 0, "successful_24h": s.get("ok") or 0,
                "tokens_24h": s.get("tokens") or 0, "audio_seconds_24h": round(s.get("audio_s") or 0, 1),
                "active": p.id == self.active_provider_id,
            })
        scopes = sorted({p.quota_scope for p in self.providers.values()})
        estimates = []
        for scope in scopes:
            models = {m for (s, m) in self.quota._usage if s == scope}  # noqa: SLF001
            for model in sorted(models):
                estimates.append({"scope": scope, "model": model, **self.quota.estimated(scope, model, now)})
        return {
            "threshold": self.quota.threshold,
            "providers": providers,
            "scopes": scopes,
            "quota": self.quota.snapshot(now),
            "estimates": estimates,
            "errors": errors,
            "active_provider_id": self.active_provider_id,
        }
