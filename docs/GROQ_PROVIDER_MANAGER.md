# Groq provider manager

Code: `backend/providers/manager.py` (`ProviderManager`, `Breaker`), `backend/providers/quota.py` (`QuotaTracker`), `backend/providers/groq_client.py` (`GroqClient`). UI: Settings → Groq API keys (`settings/GroqKeys.tsx`) and Settings → AI providers.

## Credentials vs. quota scopes

Each key ("provider") has a `quota_scope`, a free-text label for the Groq organization the key belongs to (default `default`).

- **Quota state is keyed by `(scope, model, metric)`** and is independent of credentials.
- Keys from the same Groq organization **share one quota**. Give them the same scope: freezing the scope freezes every key in it. Extra keys in one organization add failover (for example when a key is revoked) but **do not multiply quota**.
- Use different scopes only for keys from different organizations. When one scope is frozen, the manager fails over to keys in another scope.

## Credential setup

1. Create a key at `console.groq.com/keys`.
2. Settings → Groq API keys → enter a label, the key (`gsk_…`, 8–300 chars) and the scope → **Add & test**.
   - `POST /api/providers` stores the key in Windows Credential Manager as `groq:<12-hex id>`, inserts a `providers` row (reference, hint, scope, sort order), and calls `test()`.
   - `test()` calls `GET /models` with the key. On 200 it stores the set of active model ids and marks the key **healthy**. On 401 it marks the key **invalid**. Other statuses or network errors mark it **degraded**.
3. Per key you can enable or disable it, rename it, change its scope, **Replace key** (stores the new secret, resets health to `unknown`, resets the circuit breaker and clears the model list), **Test**, and **Delete** (removes the secret as well).

The model list is refreshed via `GET /api/models` when older than 6 h (`available_models()`).

## Selection: eligibility and round-robin

The keys are kept in `_order`, sorted by `(sort_order, id)`. For each request, `select(model)` scans from a **round-robin cursor** and takes the first **eligible** key, then sets `cursor = index + 1`. Consecutive requests therefore spread over all eligible keys, and ineligible keys are skipped without disturbing fairness. Selection runs under an asyncio lock.

Eligibility checks, in order (`_ineligible_reason`):

| # | Check | Reason shown |
|---|---|---|
| 1 | enabled | `disabled` |
| 2 | secret present in Credential Manager | `credential missing` |
| 3 | not marked invalid (401) | `invalid credential` |
| 4 | circuit breaker closed, or half-open with no probe in flight | `circuit open` (retry at `open_until`) |
| 5 | `(scope, model)` not frozen by any metric | `quota scope '<scope>' frozen (<metric>)` (retry at freeze end) |
| 6 | model in the key's model list (if known) | `model not available for this key` |
| 7 | model not temporarily forbidden for this key (403) | `model forbidden (403)` |

If no key is eligible, `NoProviderAvailable` carries the per-key reasons and the earliest retry time. Its user message is either "No Groq API key is configured. Add one in Settings → Groq API keys." (no keys at all) or "All Groq API keys are currently unavailable; expected to recover in about Xm Ys."

## Request loop (`ProviderManager.request`)

The loop runs up to `max_attempts` (default **4**, 1–10) attempts:

1. Select a key. If none is eligible but the earliest recovery is within `providers.max_wait_s` (default 20 s, typical for per-minute token windows), emit `providers.waiting`, sleep until then and retry (at most 3 waits; waiting does not consume an attempt). Otherwise raise `NoProviderAvailable`.
2. `breaker.on_attempt()` and `quota.begin_probe(scope, model)`. A missing secret marks the key and moves on.
3. Send (`send(client, key)`) over one shared `httpx.AsyncClient` (base URL `https://api.groq.com/openai/v1`, timeout `request_timeout_s` default 60 s, connect 10 s).

| Outcome | Handling |
|---|---|
| **2xx** | Parse rate-limit headers, end probe, breaker success, mark healthy, persist quota, return (the caller records usage when the stream ends) |
| **Timeout / network / remote-protocol error** | Breaker failure, mark degraded, log usage row, **backoff sleep**, next attempt |
| **429** | `on_rate_limited` freezes the scope+model (all same-scope keys), error logged (health unchanged), persist; **next key immediately** (no sleep) |
| **401** | Mark key **invalid** (excluded until replaced or a successful Test); next key |
| **403** | `forbidden_models[model] = now + 600` (10 min) for this key; mark degraded; next key |
| **404 with "model" in body** | Remove the model from this key's list (or forbid it for 1 h if the list is unknown); next key |
| **5xx** | Breaker failure, mark degraded, backoff, next attempt |
| **400 / other 4xx** | Raise `ProviderError("Groq rejected the request (…)")` immediately; **not retried** (redacted body excerpt) |

After the attempts are exhausted, the last error is raised.

**Backoff**: `min(8 s, 0.5 s × 2^attempt) × U(0.5, 1.0)` (jittered exponential).

**Circuit breaker** (per key): opens after **3** consecutive failures (timeouts, network errors and 5xx only). The open period is `min(300 s, 5 s × 2^(failures − 3))`, i.e. 5, 10, 20, 40 … up to 300 s. When it elapses the breaker is **half-open** and allows a single probe request (`probe_inflight`). Success closes it and resets the count; failure re-opens it with a longer period.

## Quota tracking (`QuotaTracker`)

### Provider-reported metrics (headers)

Groq documents `x-ratelimit-limit-requests` as **RPD** (requests per day) and `x-ratelimit-limit-tokens` as **TPM** (tokens per minute). On every 2xx response, `update_from_headers` reads:

| Metric | Headers |
|---|---|
| `rpd` | `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests` |
| `tpm` | `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens`, `x-ratelimit-reset-tokens` |

Reset strings are parsed by `parse_duration`: plain seconds, or combinations such as `2m59.56s`, `7.66s`, `1h2m`, `250ms`. `used_fraction = 1 − remaining/limit`.

**Threshold freeze**: if `used_fraction ≥ quota_threshold` (default **0.97**, configurable 0.5–1.0 in Settings → Groq API keys), the scope+model is frozen until the reported reset (`now + reset`). If no reset is reported, the fallback duration applies. Otherwise fresh provider data **clears** any freeze on that metric. A successful response also clears expired 429 or estimate freezes that are awaiting revalidation.

### 429 handling

`on_rate_limited` uses the `retry-after` header (parsed as a duration):
- `x-ratelimit-remaining-tokens == 0` → metric `tpm`; falls back to `x-ratelimit-reset-tokens`.
- else `x-ratelimit-remaining-requests == 0` → metric `rpd`; falls back to `x-ratelimit-reset-requests`.
- otherwise metric `429` (source `retry-after`).

The freeze lasts until `now + retry_after`, or the fallback duration if no value is given.

### Fallback freeze durations

| Metric | Fallback |
|---|---|
| `tpm`, `rpm` | 60 s |
| `rpd`, `tpd` | 3600 s |
| `ash` | 600 s |
| `asd` | 3600 s |
| `429` | 30 s |

### Probe-based recovery

Every freeze sets `needs_revalidation = True`. When the freeze time passes, the scope is **not** simply trusted again:

- `check()` reports the scope as available but `recovering`. The next request through `begin_probe` marks the entries `probing`.
- While a probe is in flight, `check()` reports the scope as frozen for 1 s (`recovering=True`). Concurrent requests wait (the request loop sleeps when recovery is within `max_wait_s`) or fail over to another scope. Only **one probe request at a time** is sent per scope+model.
- A successful probe refreshes the headers and clears `needs_revalidation`. A 429 re-freezes. `end_probe` always clears `probing`.

The dashboard shows a `revalidating` badge for entries that are recovering.

### Local estimates (RPM, TPD, ASH, ASD)

Groq does not report these in headers, so Jarvis estimates them from its own **successful** usage. Each success records `(timestamp, prompt+completion tokens, audio seconds)` per `(scope, model)` in a 24 h deque. STT counts `max(10 s, duration)`, matching Groq's 10 s billing minimum.

| Metric | Window | Weight |
|---|---|---|
| `rpm` | 60 s | 1 per request |
| `tpd` | 86400 s | tokens |
| `ash` | 3600 s | audio seconds |
| `asd` | 86400 s | audio seconds |

Limits are **user-entered** per model in Settings → AI providers → *Locally estimated limits* (`providers.local_limits[<model>] = {rpm, tpd, ash, asd}`, copied from console.groq.com → Settings → Limits). Blank values are skipped. Each estimate entry is written with `source="estimate"`. When `used ≥ threshold × limit`, the scope+model is frozen until enough of the window ages out to drop below the threshold (`_window_release`). The same limits apply to every scope that uses that model.

`estimated()` also computes local `rpd` and `tpm` counts for the dashboard table, but those two metrics freeze only from provider headers.

## Persistence

- `usage` table: one row per attempt (success or failure), with provider id, scope, model, kind (`chat`/`stt`), status code (NULL for network errors), tokens, audio seconds, latency and a redacted error (≤ 300 chars).
- `quota_state` table: upsert of every changed `(scope, model, metric)` entry (limit, remaining, reset_at, frozen_until, source, updated_at) after each request.
- `providers` table: health, last success and last error columns are updated as they change.
- On startup `load()` restores providers (checking that each secret is still present), quota entries (any with `frozen_until` start as `needs_revalidation`), and the last 24 h of successful usage for the local estimates. Freezes and estimates therefore survive restarts; `tests/backend/test_providers.py::test_quota_state_persists_across_restart` covers this.
- Clear data → *Provider usage & quota state* deletes `usage` and `quota_state` and clears the in-memory tracker.

## Dashboard (`GET /api/providers?model=`)

Availability is evaluated for `model`, which defaults to the configured complex model.

| Field | Meaning | Source |
|---|---|---|
| `threshold` | current freeze threshold | settings |
| `providers[]` | `id, label, enabled, quota_scope, key_hint, health (unknown/healthy/degraded/invalid), has_secret, circuit (closed/open/half-open), circuit_open_for_s, available, unavailable_reason, available_in_s, last_success_at, last_error, last_error_at, models, active (last used)` | manager state |
| `providers[].requests_24h, successful_24h, tokens_24h, audio_seconds_24h` | counted from the `usage` table | local |
| `scopes` | distinct scopes | |
| `quota[]` | `scope, model, metric, limit, remaining, used_fraction, reset_in_s, frozen, frozen_for_s, recovering, source, updated_at` | **`source` = `provider`** (headers), **`estimate`** (local), or **`retry-after`** (429) |
| `estimates[]` | `scope, model, rpm, rpd, tpm, tpd, ash, asd` sliding-window counts | **local estimate** |
| `errors[]` | last 50 usage rows with an error (redacted) | local |
| `active_provider_id` | key used for the latest request | |

The UI labels provider-reported bars **provider** and locally computed ones **estimate**. The `retry-after` source is currently shown with the "provider" badge. The UI refreshes when the `providers.changed` event arrives (emitted on key changes, errors and recorded usage) and runs a local 1 s timer for countdowns. It does **not** poll the backend.

## Settings

| Setting | Default | Range |
|---|---|---|
| `providers.quota_threshold` | 0.97 | 0.5–1.0 |
| `providers.request_timeout_s` | 60 | 5–300 |
| `providers.max_attempts` | 4 | 1–10 |
| `providers.models.simple / complex / summarization / transcription` | `openai/gpt-oss-20b` / `openai/gpt-oss-120b` / `openai/gpt-oss-120b` / `whisper-large-v3-turbo` | any model id; an id the provider no longer offers is replaced automatically (see *Model availability*) |
| `providers.max_wait_s` | 20 | 0–120 s |
| `providers.reasoning_effort` | `low` | low / medium / high (sent for `openai/gpt-oss-*` models) |
| `providers.local_limits` | `{}` | per model `{rpm, tpd, ash, asd}` |

Settings changes are applied live through `ProviderManager.configure()`.

## Tests

`tests/backend/test_providers.py` runs everything against `httpx.MockTransport` with no network or live keys. It covers duration parsing, the 97% threshold, per-scope/model freezes, single-probe recovery, 429 `retry-after`, local RPM and audio estimates, fair round-robin, skipping disabled and invalid keys, 429 failover across scopes, shared-scope freezing, 5xx/timeout retry and circuit opening, 400 not retried, 403 per-key forbidding, unsupported models, secrets never stored in the DB, the dashboard, and persistence across restarts.

## Model availability

`GroqClient` calls `ProviderManager.ensure_models()` before each chat or transcription request. If no enabled key has a model list yet, it fetches `GET /models` once (retried at most every 5 minutes; lists are refreshed after 6 h). `resolve_model(preferred, kind)` then returns the preferred model if any key offers it. Otherwise it substitutes the first available model from `CHAT_FALLBACKS` (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`, …) or `STT_FALLBACKS` (`whisper-large-v3-turbo`, `whisper-large-v3`) and logs a warning once. Guard, TTS and Whisper models are never chosen for chat.

This mattered in practice. On 2026-11-04 Groq no longer offered the Llama models that were the original defaults. The live key reported 11 models, so the defaults were changed to gpt-oss and the fallback was added.

## TPM is a refilling bucket

Groq's tokens-per-minute limit refills continuously, and `x-ratelimit-reset-tokens` is the time until the bucket is *completely* full. When a TPM threshold freeze triggers, Jarvis therefore does not wait for the full reset. It computes the refill rate `(limit − remaining) / reset` and unfreezes when `TPM_RECOVERY_FRACTION` (25 %) of the limit is available again. Example observed live: 8,000 TPM with 100 left and a 56 s full reset unfreezes after about 13 s. RPD freezes still last until the provider-reported reset. The usual probe-based revalidation applies after unfreezing.

## Observed limits (free tier, live key, 2026-11-04)

`openai/gpt-oss-120b`: `x-ratelimit-limit-requests` = 1000 (RPD), `x-ratelimit-limit-tokens` = 8000 (TPM). With limits this small, the token-saving measures in [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md#token-budget) matter. Several consecutive multi-step tasks can still wait 10–20 s for the bucket to refill, and the dashboard shows the freeze and its countdown while that happens.
