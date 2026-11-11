"""Quota tracking per *quota scope* (organization/project) and model.

Quota state is keyed by ``(scope, model, metric)`` and is deliberately
independent of credentials: several API keys from one Groq organization share
the same scope, so freezing the scope freezes every key in it.

Metric sources:
- ``rpd`` / ``tpm``: provider-reported via ``x-ratelimit-*`` headers (Groq
  documents limit-requests as RPD and limit-tokens as TPM).
- ``rpm`` / ``tpd`` / ``ash`` / ``asd``: locally *estimated* from our own usage
  accounting against user-entered limits, because Groq does not report them in
  headers.
- ``429``: a freeze derived from a 429 response's ``retry-after``.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Literal, Mapping

Source = Literal["provider", "estimate", "retry-after"]

_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def parse_duration(value: str | None) -> float | None:
    """Parse Groq reset strings such as ``2m59.56s``, ``7.66s``, ``1h2m``, ``250ms``."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)  # plain seconds
    except ValueError:
        pass
    total, matched = 0.0, False
    for num, unit in _DURATION.findall(value):
        matched = True
        n = float(num)
        total += {"ms": n / 1000, "s": n, "m": n * 60, "h": n * 3600}[unit]
    return total if matched else None


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


@dataclass
class QuotaEntry:
    scope: str
    model: str
    metric: str
    limit: float | None = None
    remaining: float | None = None
    reset_at: float | None = None
    frozen_until: float | None = None
    source: Source = "provider"
    updated_at: float = 0.0
    needs_revalidation: bool = False
    probing: bool = False

    @property
    def used_fraction(self) -> float | None:
        if self.limit and self.remaining is not None and self.limit > 0:
            return max(0.0, min(1.0, 1 - self.remaining / self.limit))
        return None

    def to_dict(self, now: float | None = None) -> dict:
        now = now or time.time()
        return {
            "scope": self.scope, "model": self.model, "metric": self.metric,
            "limit": self.limit, "remaining": self.remaining,
            "used_fraction": self.used_fraction,
            "reset_in_s": max(0.0, self.reset_at - now) if self.reset_at else None,
            "frozen": bool(self.frozen_until and self.frozen_until > now),
            "frozen_for_s": max(0.0, self.frozen_until - now) if self.frozen_until and self.frozen_until > now else 0.0,
            "recovering": self.needs_revalidation,
            "source": self.source, "updated_at": self.updated_at,
        }


@dataclass
class _UsageEvent:
    ts: float
    tokens: int
    audio_s: float


@dataclass
class LocalLimitSpec:
    rpm: int | None = None
    tpd: int | None = None
    ash: int | None = None
    asd: int | None = None


# A frozen TPM scope is released once this share of the per-minute budget has refilled.
TPM_RECOVERY_FRACTION = 0.25

# Fallback freeze durations when a threshold is hit but no reset time is known.
_FALLBACK_FREEZE = {"tpm": 60.0, "rpm": 60.0, "rpd": 3600.0, "tpd": 3600.0, "ash": 600.0, "asd": 3600.0, "429": 30.0}


@dataclass
class FreezeInfo:
    frozen: bool
    until: float | None = None
    metric: str | None = None
    recovering: bool = False


class QuotaTracker:
    def __init__(self, threshold: float = 0.97) -> None:
        self.threshold = threshold
        self.entries: dict[tuple[str, str, str], QuotaEntry] = {}
        self._usage: dict[tuple[str, str], deque[_UsageEvent]] = {}
        self.local_limits: dict[str, LocalLimitSpec] = {}
        self.dirty: set[tuple[str, str, str]] = set()

    # ------------------------------------------------------------------ state
    def _entry(self, scope: str, model: str, metric: str) -> QuotaEntry:
        key = (scope, model, metric)
        if key not in self.entries:
            self.entries[key] = QuotaEntry(scope, model, metric)
        return self.entries[key]

    def _freeze(self, e: QuotaEntry, until: float) -> None:
        e.frozen_until = max(e.frozen_until or 0, until)
        e.needs_revalidation = True
        e.probing = False
        self.dirty.add((e.scope, e.model, e.metric))

    def load(self, rows: Iterable[Mapping]) -> None:
        for r in rows:
            e = self._entry(r["scope"], r["model"], r["metric"])
            e.limit, e.remaining, e.reset_at = r["limit_value"], r["remaining"], r["reset_at"]
            e.frozen_until, e.source, e.updated_at = r["frozen_until"], r["source"], r["updated_at"]
            e.needs_revalidation = bool(e.frozen_until)

    def load_usage(self, rows: Iterable[Mapping]) -> None:
        for r in rows:
            self._usage.setdefault((r["scope"], r["model"]), deque()).append(
                _UsageEvent(r["created_at"], int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0), float(r["audio_seconds"] or 0)))

    # ---------------------------------------------------------- provider data
    def update_from_headers(self, scope: str, model: str, headers: Mapping[str, str], now: float | None = None) -> None:
        now = now or time.time()
        h = {k.lower(): v for k, v in headers.items()}
        for metric, suffix in (("rpd", "requests"), ("tpm", "tokens")):
            limit = _num(h.get(f"x-ratelimit-limit-{suffix}"))
            remaining = _num(h.get(f"x-ratelimit-remaining-{suffix}"))
            if limit is None and remaining is None:
                continue
            reset = parse_duration(h.get(f"x-ratelimit-reset-{suffix}"))
            e = self._entry(scope, model, metric)
            e.limit, e.remaining, e.source, e.updated_at = limit, remaining, "provider", now
            e.reset_at = now + reset if reset is not None else None
            frac = e.used_fraction
            if frac is not None and frac >= self.threshold:
                until = e.reset_at or now + _FALLBACK_FREEZE[metric]
                if metric == "tpm" and reset and limit and remaining is not None and limit > remaining:
                    # TPM is a continuously refilling bucket; the reset header is
                    # the time until it is completely full. Unfreeze once enough
                    # has refilled for a typical request (TPM_RECOVERY_FRACTION).
                    rate = (limit - remaining) / reset
                    target = TPM_RECOVERY_FRACTION * limit
                    until = min(until, now + max(1.0, (target - remaining) / rate))
                self._freeze(e, until)
            else:
                # Fresh provider data says this metric is available again.
                e.frozen_until, e.needs_revalidation, e.probing = None, False, False
            self.dirty.add((scope, model, metric))
        # A successful response also revalidates any 429/estimate freezes that
        # have already expired.
        for e in self._entries_for(scope, model):
            if e.needs_revalidation and (e.frozen_until or 0) <= now:
                e.needs_revalidation, e.probing, e.frozen_until = False, False, None
                self.dirty.add((e.scope, e.model, e.metric))

    def on_rate_limited(self, scope: str, model: str, headers: Mapping[str, str], now: float | None = None) -> float:
        """Handle HTTP 429. Returns the time the scope is frozen until."""
        now = now or time.time()
        h = {k.lower(): v for k, v in headers.items()}
        retry_after = parse_duration(h.get("retry-after"))
        metric = "429"
        if _num(h.get("x-ratelimit-remaining-tokens")) == 0:
            metric = "tpm"
            retry_after = retry_after or parse_duration(h.get("x-ratelimit-reset-tokens"))
        elif _num(h.get("x-ratelimit-remaining-requests")) == 0:
            metric = "rpd"
            retry_after = retry_after or parse_duration(h.get("x-ratelimit-reset-requests"))
        until = now + (retry_after if retry_after is not None else _FALLBACK_FREEZE[metric])
        e = self._entry(scope, model, metric)
        e.source = "retry-after" if metric == "429" else "provider"
        e.updated_at = now
        e.reset_at = until
        if metric != "429":
            e.remaining = 0
            e.limit = _num(h.get(f"x-ratelimit-limit-{'tokens' if metric == 'tpm' else 'requests'}")) or e.limit
        self._freeze(e, until)
        return until

    # ---------------------------------------------------------- local estimates
    def record_usage(self, scope: str, model: str, tokens: int = 0, audio_s: float = 0.0, now: float | None = None) -> None:
        now = now or time.time()
        dq = self._usage.setdefault((scope, model), deque())
        dq.append(_UsageEvent(now, tokens, audio_s))
        while dq and dq[0].ts < now - 86400:
            dq.popleft()
        self._check_estimates(scope, model, now)

    def estimated(self, scope: str, model: str, now: float | None = None) -> dict[str, float]:
        now = now or time.time()
        dq = self._usage.get((scope, model), deque())
        minute = [e for e in dq if e.ts >= now - 60]
        hour = [e for e in dq if e.ts >= now - 3600]
        day = [e for e in dq if e.ts >= now - 86400]
        return {
            "rpm": float(len(minute)),
            "rpd": float(len(day)),
            "tpm": float(sum(e.tokens for e in minute)),
            "tpd": float(sum(e.tokens for e in day)),
            "ash": sum(e.audio_s for e in hour),
            "asd": sum(e.audio_s for e in day),
        }

    def _check_estimates(self, scope: str, model: str, now: float) -> None:
        spec = self.local_limits.get(model)
        if not spec:
            return
        dq = self._usage.get((scope, model), deque())
        windows = {"rpm": 60, "tpd": 86400, "ash": 3600, "asd": 86400}
        est = self.estimated(scope, model, now)
        for metric, window in windows.items():
            limit = getattr(spec, metric)
            if not limit:
                continue
            used = est[metric]
            e = self._entry(scope, model, metric)
            e.limit, e.remaining, e.source, e.updated_at = float(limit), max(0.0, limit - used), "estimate", now
            if used >= self.threshold * limit:
                # Frozen until enough of the window ages out to drop below threshold.
                until = self._window_release(dq, metric, window, limit, now)
                e.reset_at = until
                self._freeze(e, until)
            elif e.frozen_until and e.frozen_until <= now:
                e.frozen_until, e.needs_revalidation = None, False
            self.dirty.add((scope, model, metric))

    def _window_release(self, dq: deque[_UsageEvent], metric: str, window: int, limit: float, now: float) -> float:
        events = [e for e in dq if e.ts >= now - window]
        weight = {"rpm": lambda e: 1.0, "tpd": lambda e: float(e.tokens), "ash": lambda e: e.audio_s, "asd": lambda e: e.audio_s}[metric]
        used = sum(weight(e) for e in events)
        for e in events:
            used -= weight(e)
            if used < self.threshold * limit:
                return e.ts + window
        return now + window

    # --------------------------------------------------------------- queries
    def _entries_for(self, scope: str, model: str) -> list[QuotaEntry]:
        return [e for (s, m, _), e in self.entries.items() if s == scope and m == model]

    def check(self, scope: str, model: str, now: float | None = None) -> FreezeInfo:
        """Is this scope/model available for a new request?"""
        now = now or time.time()
        latest: QuotaEntry | None = None
        recovering = False
        for e in self._entries_for(scope, model):
            if e.frozen_until and e.frozen_until > now:
                if latest is None or (e.frozen_until > (latest.frozen_until or 0)):
                    latest = e
            elif e.needs_revalidation:
                recovering = True
                if e.probing:
                    # One probe request at a time while revalidating after a reset.
                    return FreezeInfo(True, now + 1.0, e.metric, recovering=True)
        if latest:
            return FreezeInfo(True, latest.frozen_until, latest.metric)
        return FreezeInfo(False, recovering=recovering)

    def begin_probe(self, scope: str, model: str) -> None:
        for e in self._entries_for(scope, model):
            if e.needs_revalidation:
                e.probing = True

    def end_probe(self, scope: str, model: str) -> None:
        for e in self._entries_for(scope, model):
            e.probing = False

    def snapshot(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [e.to_dict(now) for e in self.entries.values()]

    def take_dirty(self) -> list[QuotaEntry]:
        out = [self.entries[k] for k in self.dirty if k in self.entries]
        self.dirty.clear()
        return out

    def clear_scope(self, scope: str) -> None:
        for key in [k for k in self.entries if k[0] == scope]:
            del self.entries[key]
