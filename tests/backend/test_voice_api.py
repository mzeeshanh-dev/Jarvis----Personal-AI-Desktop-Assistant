"""Voice pipeline (VAD, noise reduction, wake word, capture->STT->agent),
API auth/validation, Gmail parsing, secret redaction."""
from __future__ import annotations

import asyncio
import base64
import io
import wave

import numpy as np
import pytest

from conftest import ROOT, text_completion
from core.secrets import redact
from voice.audio import FRAME, SAMPLE_RATE, rms_level, to_wav
from voice.noise import reduce_noise
from voice.vad import EnergyVAD, SileroVAD, load_vad

MODELS = ROOT / "resources" / "models"


def tone(seconds: float, freq: float = 220.0, amp: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    # vowel-like harmonic stack with amplitude modulation (speech-ish for VAD)
    sig = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 8)) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    return (sig / np.max(np.abs(sig)) * amp * 32767).astype(np.int16)


def frames(audio: np.ndarray) -> list[np.ndarray]:
    return [audio[i:i + FRAME] for i in range(0, len(audio) - FRAME + 1, FRAME)]


# ---------------------------------------------------------------- audio DSP
def test_wav_encoding_roundtrip():
    audio = tone(0.5)
    with wave.open(io.BytesIO(to_wav(audio))) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getnframes() == len(audio)


def test_rms_level_bounds():
    assert rms_level(np.zeros(FRAME, dtype=np.int16)) == 0.0
    assert 0.5 < rms_level(tone(0.1)[:FRAME]) <= 1.0


def test_noise_reduction_attenuates_noise_keeps_signal():
    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 0.03, SAMPLE_RATE * 2) * 32767).astype(np.int16)
    speech = np.concatenate([np.zeros(SAMPLE_RATE, dtype=np.int16), tone(1.0, amp=0.3)])
    noisy = np.clip(speech.astype(np.int32) + noise, -32768, 32767).astype(np.int16)
    out = reduce_noise(noisy)
    assert out.shape == noisy.shape and out.dtype == np.int16
    noise_before = np.std(noisy[: SAMPLE_RATE // 2].astype(float))
    noise_after = np.std(out[: SAMPLE_RATE // 2].astype(float))
    assert noise_after < noise_before * 0.6
    sig_after = np.std(out[SAMPLE_RATE + 1000: SAMPLE_RATE * 2 - 1000].astype(float))
    assert sig_after > np.std(speech[SAMPLE_RATE:].astype(float)) * 0.5


@pytest.mark.skipif(not (MODELS / "vad" / "silero_vad.onnx").exists(), reason="Silero model not downloaded")
def test_silero_vad_silence_is_low():
    vad = SileroVAD(MODELS / "vad" / "silero_vad.onnx")
    probs = [vad(f) for f in frames(np.zeros(SAMPLE_RATE, dtype=np.int16))]
    assert max(probs) < 0.2


def test_energy_vad_detects_loud_frames():
    vad = EnergyVAD()
    rng = np.random.default_rng(1)
    for f in frames((rng.normal(0, 0.002, SAMPLE_RATE) * 32767).astype(np.int16)):
        vad(f)
    assert vad(tone(0.1)[:FRAME]) > 0.8


def test_load_vad_falls_back():
    assert isinstance(load_vad(None), EnergyVAD)


@pytest.mark.skipif(not (MODELS / "wakeword" / "hey_jarvis_v0.1.onnx").exists(), reason="wake-word models not downloaded")
def test_wakeword_runs_and_ignores_silence():
    from voice.wakeword import WakeWordDetector
    w = MODELS / "wakeword"
    det = WakeWordDetector(w / "melspectrogram.onnx", w / "embedding_model.onnx", w / "hey_jarvis_v0.1.onnx")
    rng = np.random.default_rng(2)
    audio = (rng.normal(0, 0.01, SAMPLE_RATE * 3) * 32767).astype(np.int16)
    scores = [s for f in frames(audio) if (s := det.process(f)) is not None]
    assert len(scores) > 20  # one score per 80 ms chunk
    assert not any(det.detected(s) for s in scores)


@pytest.mark.skipif(not (MODELS / "piper" / "en_GB-alan-medium.onnx").exists(), reason="Piper voice not downloaded")
async def test_piper_tts_synthesizes_wav():
    from voice.tts import PiperTTS
    tts = PiperTTS()
    clip_id, wav, ms = await tts.synthesize(MODELS / "piper" / "en_GB-alan-medium.onnx", "Opening Chrome.")
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() / w.getframerate() > 0.4
    assert clip_id in tts.cache


# --------------------------------------------- voice session (fake microphone)
class FakeMic:
    def __init__(self, loop, audio: np.ndarray):
        self.loop, self.audio, self.device = loop, audio, None
        self._subs = []
        self.active = False
        self.opened = 0

    def subscribe(self, maxsize=400):
        q = asyncio.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q):
        self._subs.remove(q)

    @property
    def subscriber_count(self):
        return len(self._subs)

    def open(self):
        self.active = True
        self.opened += 1
        for f in frames(self.audio):
            for q in self._subs:
                q.put_nowait(f)

    def close(self):
        self.active = False


def test_push_to_talk_pipeline(paths, secrets, groq, monkeypatch):
    """Mic -> VAD -> noise reduction -> Groq Whisper (mocked) -> agent -> reply event."""
    from fastapi.testclient import TestClient
    from core.app import create_app
    import voice.pipeline as vp

    speech = np.concatenate([np.zeros(SAMPLE_RATE // 2, dtype=np.int16), tone(1.2), np.zeros(SAMPLE_RATE * 2, dtype=np.int16)])
    app = create_app(paths=paths, secrets=secrets, groq_transport=groq.transport, auth_token="tok", allowed_origins=["app://jarvis"])
    with TestClient(app) as c:
        c.headers["X-Jarvis-Token"] = "tok"
        c.put("/api/settings", json={"voice": {"tts_engine": "off", "silence_ms": 600}})
        c.post("/api/providers", json={"label": "k", "api_key": "gsk_test_key_abcd"})
        voice = app.state.services.voice
        voice._vad = EnergyVAD()  # deterministic on synthetic audio
        monkeypatch.setattr(voice, "_get_mic", lambda: FakeMic(asyncio.get_event_loop(), speech))
        groq.push(body={"text": "list my running applications"})
        groq.push(body=text_completion("You have 3 apps open."))
        with c.websocket_connect("/ws?token=tok", headers={"origin": "app://jarvis"}) as ws:
            ws.receive_json()
            ws.send_json({"type": "voice.toggle"})
            seen = []
            while True:
                e = ws.receive_json()
                seen.append(e)
                if e["type"] == "task.finished":
                    break
    types = [e["type"] for e in seen]
    states = [e["state"] for e in seen if e["type"] == "voice.state"]
    assert states[:3] == ["listening", "transcribing", "idle"]
    assert "voice.level" in types
    tr = next(e for e in seen if e["type"] == "voice.transcript")
    assert tr["text"] == "list my running applications"
    started = next(e for e in seen if e["type"] == "task.started")
    assert started["source"] == "voice"
    stt_req = next(r for r in groq.requests if not r.url.path.endswith("/models"))
    assert stt_req.url.path.endswith("/audio/transcriptions") and b"RIFF" in stt_req.content


# ------------------------------------------------------------------ API/auth
@pytest.fixture
def api(paths, secrets, groq):
    from fastapi.testclient import TestClient
    from core.app import create_app
    app = create_app(paths=paths, secrets=secrets, groq_transport=groq.transport, auth_token="tok", allowed_origins=["app://jarvis"], enable_voice=False)
    with TestClient(app) as c:
        yield c


def test_health_is_public_rest_needs_token(api):
    assert api.get("/health").json()["ok"]
    assert api.get("/api/settings").status_code == 401
    assert api.get("/api/settings", headers={"X-Jarvis-Token": "wrong"}).status_code == 401
    assert api.get("/api/settings", headers={"X-Jarvis-Token": "tok"}).status_code == 200


def test_websocket_requires_token_and_origin(api):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect("/ws?token=nope") as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect("/ws?token=tok", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with api.websocket_connect("/ws?token=tok", headers={"origin": "app://jarvis"}) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        ws.send_json({"type": "bogus"})
        assert ws.receive_json()["type"] == "error"


def test_settings_validation(api):
    h = {"X-Jarvis-Token": "tok"}
    r = api.put("/api/settings", json={"providers": {"quota_threshold": 1.5}}, headers=h)
    assert r.status_code == 422
    r = api.put("/api/settings", json={"providers": {"quota_threshold": 0.9}}, headers=h)
    assert r.json()["providers"]["quota_threshold"] == 0.9
    assert api.get("/api/settings", headers=h).json()["providers"]["quota_threshold"] == 0.9


def test_provider_api_never_returns_secret(api):
    h = {"X-Jarvis-Token": "tok"}
    r = api.post("/api/providers", json={"label": "main", "api_key": "gsk_live_secret_abcdef123456"}, headers=h)
    assert r.status_code == 200 and "gsk_live_secret_abcdef123456" not in r.text
    d = api.get("/api/providers", headers=h)
    assert "gsk_live_secret_abcdef123456" not in d.text and d.json()["providers"][0]["key_hint"] == "gsk_…3456"


def test_clear_data(api):
    h = {"X-Jarvis-Token": "tok"}
    r = api.post("/api/data/clear", json={"conversations": True, "task_history": True}, headers=h)
    assert set(r.json()["cleared"]) == {"conversations", "task_history"}


def test_redaction():
    s = redact('key gsk_abcdefghijklmnop and Bearer abc.def.ghi123 "refresh_token": "1//xyz"')
    assert "gsk_abcdefghijklmnop" not in s and "abc.def.ghi123" not in s and "1//xyz" not in s
    line = redact('127.0.0.1:5 - "WebSocket /ws?token=b30b495b9f53f0eea65be5cf" [accepted]')
    assert "b30b495b9f53f0eea65be5cf" not in line and "/ws?token=[REDACTED]" in line


# -------------------------------------------------------------------- gmail
def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_gmail_body_extraction():
    from integrations.gmail import extract_body
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"data": _b64("<p>Hello <b>there</b></p><script>x()</script>")}},
        {"mimeType": "application/pdf", "filename": "a.pdf", "body": {}},
    ]}
    assert extract_body(payload) == "Hello there"
    payload["parts"].append({"mimeType": "text/plain", "body": {"data": _b64("Plain wins")}})
    assert extract_body(payload) == "Plain wins"


async def test_gmail_search_and_draft(db, secrets):
    import httpx
    from integrations.gmail import REF_CLIENT_ID, REF_CLIENT_SECRET, REF_REFRESH, GmailService
    await secrets.set(REF_CLIENT_ID, "x.apps.googleusercontent.com")
    await secrets.set(REF_CLIENT_SECRET, "s")
    await secrets.set(REF_REFRESH, "1//refresh")
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "ya29.token", "expires_in": 3600})
        if req.url.path.endswith("/messages"):
            assert req.url.params["q"] == "GreenBot"
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        if "/messages/" in req.url.path:
            mid = req.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"id": mid, "threadId": "t" + mid, "snippet": "update &amp; notes", "labelIds": ["UNREAD"],
                                             "payload": {"headers": [{"name": "Subject", "value": f"GreenBot {mid}"}, {"name": "From", "value": "a@b.com"}]}})
        if req.url.path.endswith("/drafts"):
            return httpx.Response(200, json={"id": "d1", "message": {"id": "m9"}})
        return httpx.Response(404)

    g = GmailService(db, secrets, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await g.search("GreenBot", 5)
    assert [r["subject"] for r in res] == ["GreenBot m1", "GreenBot m2"] and res[0]["snippet"] == "update & notes"
    d = await g.create_draft(["x@y.com"], "Hi", "Body")
    assert d["draft_id"] == "d1"
    assert all("Bearer ya29.token" == r.headers.get("authorization") for r in sent if r.url.host == "gmail.googleapis.com")
    await g.close()


async def test_gmail_oauth_state_is_validated(db, secrets):
    from integrations.gmail import GmailError, GmailService
    g = GmailService(db, secrets)
    await g.configure("123-abc.apps.googleusercontent.com", "secret")
    url = await g.start_auth("http://127.0.0.1:5000/api/gmail/oauth/callback")
    assert "code_challenge_method=S256" in url and "gmail.readonly" in url
    with pytest.raises(GmailError):
        await g.complete_auth("code", "forged-state")
    await g.close()
