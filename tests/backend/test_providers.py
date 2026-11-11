"""Groq provider manager: round-robin, quota freezing, shared scopes,
recovery, 429/401/403/5xx/timeout handling, circuit breaker."""
from __future__ import annotations

import httpx
import pytest

from conftest import rl_headers, text_completion
from providers.groq_client import GroqClient
from providers.manager import NoProviderAvailable, ProviderError
from providers.quota import QuotaTracker, parse_duration

MODEL = "llama-3.3-70b-versatile"
MSGS = [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize("raw,expected", [
    ("2m59.56s", 179.56), ("7.66s", 7.66), ("1h2m3s", 3723), ("250ms", 0.25), ("12", 12.0), ("", None), (None, None), ("abc", None),
])
def test_parse_duration(raw, expected):
    got = parse_duration(raw)
    assert got == pytest.approx(expected) if expected is not None else got is None


def test_threshold_freezes_at_97_percent(clock):
    q = QuotaTracker(0.97)
    q.update_from_headers("org", MODEL, rl_headers(remaining_req=500, limit_req=14400))
    assert not q.check("org", MODEL).frozen
    # 14400 * 3% = 432 remaining -> exactly at the threshold
    q.update_from_headers("org", MODEL, rl_headers(remaining_req=432, limit_req=14400, reset_req="10m"))
    f = q.check("org", MODEL)
    assert f.frozen and f.metric == "rpd"
    assert f.until == pytest.approx(clock.now + 600)


def test_tpm_threshold_uses_token_reset(clock):
    q = QuotaTracker(0.97)
    q.update_from_headers("org", MODEL, rl_headers(remaining_tok=100, limit_tok=6000, reset_tok="7.5s"))
    f = q.check("org", MODEL)
    # Bucket refills 5900 tokens in 7.5 s; 25 % of 6000 (1500) is available after (1500-100)/786.7 s.
    assert f.frozen and f.metric == "tpm" and f.until == pytest.approx(clock.now + 1400 / (5900 / 7.5))


def test_tpm_freeze_is_shorter_than_full_bucket_reset(clock):
    # Real values observed from Groq: 8000 TPM, 100 left, bucket full again in 56 s.
    q = QuotaTracker(0.97)
    q.update_from_headers("org", MODEL, rl_headers(remaining_tok=100, limit_tok=8000, reset_tok="56s"))
    until = q.check("org", MODEL).until
    assert 10 < until - clock.now < 15  # ~13.4 s, not 56 s
    # RPD (daily) still freezes for the full provider-reported reset.
    q.update_from_headers("org2", MODEL, rl_headers(remaining_req=10, limit_req=1000, reset_req="3h"))
    assert q.check("org2", MODEL).until == pytest.approx(clock.now + 10800)


def test_threshold_is_configurable(clock):
    q = QuotaTracker(0.5)
    q.update_from_headers("org", MODEL, rl_headers(remaining_req=7000, limit_req=14400))
    assert q.check("org", MODEL).frozen


def test_freeze_is_per_model_and_scope(clock):
    q = QuotaTracker()
    q.update_from_headers("org", MODEL, rl_headers(remaining_req=1, limit_req=100))
    assert q.check("org", MODEL).frozen
    assert not q.check("org", "llama-3.1-8b-instant").frozen
    assert not q.check("other-org", MODEL).frozen


def test_recovery_requires_single_probe(clock):
    q = QuotaTracker()
    q.update_from_headers("org", MODEL, rl_headers(remaining_tok=10, limit_tok=6000, reset_tok="5s"))
    assert q.check("org", MODEL).frozen
    clock.advance(6)
    f = q.check("org", MODEL)
    assert not f.frozen and f.recovering  # eligible again, pending revalidation
    q.begin_probe("org", MODEL)
    assert q.check("org", MODEL).frozen  # only one probe in flight
    q.update_from_headers("org", MODEL, rl_headers())  # probe succeeded with healthy headers
    f = q.check("org", MODEL)
    assert not f.frozen and not f.recovering


def test_429_uses_retry_after(clock):
    q = QuotaTracker()
    until = q.on_rate_limited("org", MODEL, {"retry-after": "12"})
    assert until == pytest.approx(clock.now + 12)
    assert q.check("org", MODEL).frozen
    clock.advance(13)
    assert not q.check("org", MODEL).frozen


def test_local_estimates_freeze_rpm(clock):
    from providers.quota import LocalLimitSpec
    q = QuotaTracker(0.97)
    q.local_limits = {MODEL: LocalLimitSpec(rpm=30)}
    for _ in range(29):
        q.record_usage("org", MODEL, tokens=10)
    assert not q.check("org", MODEL).frozen  # 29/30 = 96.7% < 97%
    q.record_usage("org", MODEL, tokens=10)
    f = q.check("org", MODEL)
    assert f.frozen and f.metric == "rpm"
    entry = q.entries[("org", MODEL, "rpm")]
    assert entry.source == "estimate"
    clock.advance(61)
    q.record_usage("org", "other-model")  # unrelated
    assert not q.check("org", MODEL).frozen


def test_audio_seconds_estimates(clock):
    from providers.quota import LocalLimitSpec
    q = QuotaTracker(0.97)
    q.local_limits = {"whisper-large-v3-turbo": LocalLimitSpec(ash=100)}
    q.record_usage("org", "whisper-large-v3-turbo", audio_s=60)
    assert not q.check("org", "whisper-large-v3-turbo").frozen
    q.record_usage("org", "whisper-large-v3-turbo", audio_s=40)
    assert q.check("org", "whisper-large-v3-turbo").frozen


# --------------------------------------------------------------- manager
async def _three_keys(make_manager, scopes=("org-a", "org-b", "org-c")):
    m = make_manager()
    for i, scope in enumerate(scopes):
        await m.add(f"key{i}", f"gsk_test_key_{i:04d}", scope)
    return m


async def test_round_robin_is_fair(make_manager, groq, clock):
    m = await _three_keys(make_manager)
    client = GroqClient(m)
    for _ in range(6):
        groq.push(body=text_completion("ok"))
        await client.chat(model=MODEL, messages=MSGS)
    used = groq.keys_used()
    assert used == ["gsk_test_key_0000", "gsk_test_key_0001", "gsk_test_key_0002"] * 2
    await m.close()


async def test_disabled_and_invalid_keys_are_skipped(make_manager, groq, clock):
    m = await _three_keys(make_manager)
    ids = list(m.providers)
    await m.update(ids[0], enabled=False)
    groq.push(status=401, body={"error": "bad"})
    groq.push(body=text_completion("ok"))
    res = await GroqClient(m).chat(model=MODEL, messages=MSGS)
    assert res.content == "ok"
    assert m.providers[ids[1]].health == "invalid"  # 401 marked invalid
    assert groq.keys_used() == ["gsk_test_key_0001", "gsk_test_key_0002"]
    groq.push(body=text_completion("again"))
    await GroqClient(m).chat(model=MODEL, messages=MSGS)
    assert groq.keys_used()[-1] == "gsk_test_key_0002"  # key0 disabled, key1 invalid
    await m.close()


async def test_429_fails_over_to_other_scope(make_manager, groq, clock):
    m = await _three_keys(make_manager)
    groq.push(status=429, headers={"retry-after": "30", **rl_headers(remaining_tok=0)}, body={"error": "rate"})
    groq.push(body=text_completion("ok"))
    res = await GroqClient(m).chat(model=MODEL, messages=MSGS)
    assert res.content == "ok"
    assert m.quota.check("org-a", MODEL).frozen
    assert not m.quota.check("org-b", MODEL).frozen
    await m.close()


async def test_shared_org_scope_freezes_all_its_keys(make_manager, groq, clock):
    m = await _three_keys(make_manager, scopes=("shared", "shared", "shared"))
    groq.push(status=429, headers={"retry-after": "45"}, body={"error": "rate"})
    with pytest.raises(NoProviderAvailable) as exc:
        await GroqClient(m).chat(model=MODEL, messages=MSGS)
    # One 429 froze the shared scope; other keys of the same org were NOT tried.
    assert len(groq.keys_used()) == 1
    assert exc.value.retry_at == pytest.approx(clock.now + 45)
    assert "45s" in exc.value.user_message() or "0m 45s" in exc.value.user_message()
    await m.close()


async def test_threshold_freeze_switches_scope_then_recovers(make_manager, groq, clock):
    m = await _three_keys(make_manager, scopes=("shared", "shared", "solo"))
    client = GroqClient(m)
    groq.push(headers=rl_headers(remaining_req=100, limit_req=14400, reset_req="2m"), body=text_completion("a"))  # 99.3% used
    await client.chat(model=MODEL, messages=MSGS)
    assert m.quota.check("shared", MODEL).frozen
    for _ in range(3):
        groq.push(body=text_completion("b"))
        await client.chat(model=MODEL, messages=MSGS)
    assert groq.keys_used()[1:] == ["gsk_test_key_0002"] * 3  # only the other org's key
    clock.advance(121)
    groq.push(body=text_completion("c"))
    groq.push(body=text_completion("d"))
    await client.chat(model=MODEL, messages=MSGS)
    await client.chat(model=MODEL, messages=MSGS)
    assert not m.quota.check("shared", MODEL).frozen
    assert {"gsk_test_key_0000", "gsk_test_key_0001"} & set(groq.keys_used()[-2:])
    await m.close()


async def test_5xx_and_timeout_retry_then_open_circuit(make_manager, groq, clock):
    m = make_manager(max_attempts=3)
    await m.add("only", "gsk_test_key_solo", "org")
    pid = next(iter(m.providers))

    def timeout(_req):
        raise httpx.ConnectTimeout("slow")
    groq.queue += [(503, {}, "down"), timeout, (500, {}, "err")]
    with pytest.raises(ProviderError):
        await GroqClient(m).chat(model=MODEL, messages=MSGS)
    p = m.providers[pid]
    assert p.breaker.failures == 3 and p.breaker.state(clock.now) == "open"
    with pytest.raises(NoProviderAvailable):
        m.select(MODEL)
    clock.advance(6)  # breaker half-open after 5 s
    groq.push(body=text_completion("recovered"))
    res = await GroqClient(m).chat(model=MODEL, messages=MSGS)
    assert res.content == "recovered" and p.breaker.state(clock.now) == "closed"
    await m.close()


async def test_400_is_not_retried(make_manager, groq, clock):
    m = await _three_keys(make_manager)
    groq.push(status=400, body={"error": {"message": "bad request"}})
    with pytest.raises(ProviderError) as exc:
        await GroqClient(m).chat(model=MODEL, messages=MSGS)
    assert exc.value.status == 400 and len(groq.keys_used()) == 1
    await m.close()


async def test_403_marks_model_forbidden_for_that_key(make_manager, groq, clock):
    m = await _three_keys(make_manager)
    groq.push(status=403, body={"error": "forbidden"})
    groq.push(body=text_completion("ok"))
    await GroqClient(m).chat(model=MODEL, messages=MSGS)
    first = m.providers[list(m.providers)[0]]
    assert MODEL in first.forbidden_models
    reason, _ = m._ineligible_reason(first, MODEL, clock.now)
    assert reason and "403" in reason
    await m.close()


async def test_no_keys_message(make_manager):
    m = make_manager()
    with pytest.raises(NoProviderAvailable) as exc:
        m.select(MODEL)
    assert "No Groq API key" in exc.value.user_message()
    await m.close()


async def test_test_key_validates_and_lists_models(make_manager, groq):
    m = make_manager()
    good = await m.add("good", "gsk_good_key_1234")
    bad = await m.add("bad", "bad-key-000")
    assert (await m.test(good.id))["ok"]
    r = await m.test(bad.id)
    assert not r["ok"] and m.providers[bad.id].health == "invalid"
    assert MODEL in m.providers[good.id].models
    await m.close()


async def test_unsupported_model_is_ineligible(make_manager, groq, clock):
    m = make_manager()
    p = await m.add("k", "gsk_good_key_1234")
    await m.test(p.id)
    with pytest.raises(NoProviderAvailable) as exc:
        m.select("nonexistent-model")
    assert "model not available" in next(iter(exc.value.reasons.values()))
    await m.close()


async def test_unavailable_configured_model_falls_back(make_manager, groq, clock):
    groq.models = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "whisper-large-v3", "meta-llama/llama-prompt-guard-2-86m"]
    m = make_manager()
    await m.add("k", "gsk_good_key_1234")
    groq.push(body=text_completion("ok"))
    await GroqClient(m).chat(model="llama-3.3-70b-versatile", messages=MSGS)  # retired model
    import json as _json
    sent = _json.loads(next(r for r in groq.requests if r.url.path.endswith("/chat/completions")).content)
    assert sent["model"] == "openai/gpt-oss-120b"
    assert m.resolve_model("whisper-large-v3-turbo", "stt") == "whisper-large-v3"
    assert m.resolve_model("openai/gpt-oss-20b") == "openai/gpt-oss-20b"
    await m.close()


async def test_secrets_never_in_database(make_manager, db):
    m = make_manager()
    await m.add("k", "gsk_supersecret_value_9999")
    rows = await db.fetchall("SELECT * FROM providers")
    assert "gsk_supersecret_value_9999" not in str(rows)
    assert rows[0]["credential_ref"].startswith("groq:")
    await m.close()


async def test_usage_accounting_and_dashboard(make_manager, groq, clock):
    m = await _three_keys(make_manager)
    groq.push(body=text_completion("ok", usage=(120, 30)))
    await GroqClient(m).chat(model=MODEL, messages=MSGS)
    d = await m.dashboard(MODEL)
    p0 = d["providers"][0]
    assert p0["tokens_24h"] == 150 and p0["successful_24h"] == 1 and p0["available"]
    assert any(q["metric"] == "rpd" and q["source"] == "provider" for q in d["quota"])
    await m.close()


async def test_quota_state_persists_across_restart(make_manager, groq, clock):
    m = await _three_keys(make_manager, scopes=("org", "org", "org"))
    groq.push(status=429, headers={"retry-after": "300"}, body={"error": "rate"})
    with pytest.raises(NoProviderAvailable):
        await GroqClient(m).chat(model=MODEL, messages=MSGS)
    await m.close()
    m2 = make_manager()
    await m2.load()
    assert m2.quota.check("org", MODEL).frozen
    await m2.close()
