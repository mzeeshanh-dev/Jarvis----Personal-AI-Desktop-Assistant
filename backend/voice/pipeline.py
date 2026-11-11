"""Voice service: push-to-talk and wake-word sessions.

Microphone -> (VAD-gated capture) -> noise reduction -> Groq Whisper STT -> agent
Wake word runs locally; audio is uploaded only for a captured utterance.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from typing import TYPE_CHECKING, Any

import numpy as np

from providers.manager import NoProviderAvailable, ProviderError
from voice.audio import FRAME, SAMPLE_RATE, MicrophoneError, MicrophoneStream, rms_level, to_wav
from voice.noise import reduce_noise
from voice.tts import PiperTTS
from voice.vad import EnergyVAD, SileroVAD, load_vad  # noqa: F401

if TYPE_CHECKING:
    from agent.orchestrator import Orchestrator
    from agent.services import Services

log = logging.getLogger(__name__)

FRAME_MS = FRAME * 1000 / SAMPLE_RATE
# Whisper commonly hallucinates these on near-silent input.
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", ".", "so", "okay",
                  "subtitles by the amara.org community", "please subscribe"}


class VoiceService:
    def __init__(self, services: "Services", orchestrator: "Orchestrator") -> None:
        self.s = services
        self.orchestrator = orchestrator
        self.state = "idle"
        self.conversation_id: str | None = None
        self._mic: MicrophoneStream | None = None
        self._session: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._vad: SileroVAD | EnergyVAD | None = None
        self._wake: Any = None
        self._wake_task: asyncio.Task[None] | None = None
        self._wake_error: str | None = None
        self.tts = PiperTTS()
        self.paused = False

    # --------------------------------------------------------------- state
    async def _set_state(self, state: str, **extra: Any) -> None:
        self.state = state
        await self.s.events.emit("voice.state", state=state, wake_word=self._wake_task is not None, paused=self.paused, **extra)

    def status(self) -> dict[str, Any]:
        v = self.s.settings.voice
        return {
            "state": self.state, "paused": self.paused,
            "wake_word_enabled": v.wake_word_enabled, "wake_word_running": self._wake_task is not None,
            "wake_word_error": self._wake_error,
            "vad": self._vad.name if self._vad else ("silero" if self.s.paths.model_file("vad", "silero_vad.onnx") else "energy"),
            "piper_available": self.tts.available(self._piper_model()),
            "piper_voice": v.piper_voice, "mic_open": bool(self._mic and self._mic.active),
        }

    def _piper_model(self):  # noqa: ANN202
        return self.s.paths.model_file("piper", f"{self.s.settings.voice.piper_voice}.onnx")

    def _get_mic(self) -> MicrophoneStream:
        if self._mic is None:
            self._mic = MicrophoneStream(asyncio.get_running_loop(), self.s.settings.voice.input_device)
        return self._mic

    def _release_mic_if_idle(self) -> None:
        if self._mic and self._mic.subscriber_count == 0:
            self._mic.close()
            self._mic = None

    # ---------------------------------------------------------- push-to-talk
    async def toggle(self, conversation_id: str | None = None) -> str:
        if self._session and not self._session.done():
            if self.state == "listening":
                self._stop.set()
                return "stopping"
            return self.state
        await self.start(conversation_id)
        return "listening"

    async def start(self, conversation_id: str | None = None, source: str = "ptt", interrupt: bool = True) -> None:
        if conversation_id:
            self.conversation_id = conversation_id
        if self._session and not self._session.done():
            return
        if interrupt:
            await self.s.events.emit("tts.stop")  # barge-in: stop any speech
        self._stop = asyncio.Event()
        self._session = asyncio.create_task(self._run_session(source), name="voice-session")

    async def stop(self) -> None:
        self._stop.set()

    async def cancel(self) -> None:
        if self._session and not self._session.done():
            self._session.cancel()
            try:
                await self._session
            except asyncio.CancelledError:
                pass

    def _ensure_vad(self) -> SileroVAD | EnergyVAD:
        if self._vad is None:
            self._vad = load_vad(self.s.paths.model_file("vad", "silero_vad.onnx"))
        return self._vad

    async def _run_session(self, source: str) -> None:
        v = self.s.settings.voice
        mic = self._get_mic()
        q = mic.subscribe()
        try:
            try:
                mic.open()
            except MicrophoneError as exc:
                await self._set_state("error", error=str(exc))
                await self.s.events.emit("voice.error", error=str(exc))
                return
            vad = self._ensure_vad()
            vad.reset()
            self._prewarm_tts()
            await self._set_state("listening", source=source)
            audio = await self._capture(q, vad, v)
        except asyncio.CancelledError:
            await self._set_state("idle")
            raise
        finally:
            mic.unsubscribe(q)
            self._release_mic_if_idle()
        if audio is None:
            await self._set_state("idle")
            return
        await self._transcribe_and_submit(audio, v)

    async def _capture(self, q: asyncio.Queue[np.ndarray], vad: Any, v: Any) -> np.ndarray | None:
        preroll: deque[np.ndarray] = deque(maxlen=10)
        frames: list[np.ndarray] = []
        speech = False
        silence_ms = 0.0
        speech_ms = 0.0
        started = time.monotonic()
        last_level = 0.0
        thr = v.vad_threshold
        while True:
            try:
                frame = await asyncio.wait_for(q.get(), timeout=1.5)
            except asyncio.TimeoutError:
                await self.s.events.emit("voice.error", error="Microphone stopped delivering audio.")
                return None
            now = time.monotonic()
            if now - last_level > 0.08:  # ~12 Hz meter updates
                last_level = now
                await self.s.events.emit("voice.level", level=round(rms_level(frame), 3))
            prob = vad(frame)  # Silero: <1 ms per 32 ms frame on CPU
            if not speech:
                preroll.append(frame)
                if prob >= thr:
                    speech = True
                    frames.extend(preroll)
                elif self._stop.is_set():
                    return None
                elif now - started > v.no_speech_timeout_s:
                    await self.s.events.emit("voice.no_speech")
                    return None
                continue
            frames.append(frame)
            speech_ms += FRAME_MS
            silence_ms = silence_ms + FRAME_MS if prob < max(0.15, thr - 0.15) else 0.0
            if silence_ms >= v.silence_ms or now - started > v.max_record_s or self._stop.is_set():
                break
        if speech_ms < 250:
            await self.s.events.emit("voice.no_speech")
            return None
        return np.concatenate(frames)

    async def _transcribe_and_submit(self, audio: np.ndarray, v: Any) -> None:
        await self._set_state("transcribing")
        t0 = time.perf_counter()
        if v.noise_reduction:
            audio = await asyncio.to_thread(reduce_noise, audio)
        wav = to_wav(audio)
        duration = len(audio) / SAMPLE_RATE
        try:
            lang = None if v.language in ("", "auto") else v.language
            text, latency = await self.s.groq.transcribe(wav, model=self.s.settings.providers.models.transcription,
                                                         language=lang, duration_s=duration)
        except NoProviderAvailable as exc:
            await self._set_state("idle")
            await self.s.events.emit("voice.error", error=exc.user_message())
            return
        except ProviderError as exc:
            await self._set_state("idle")
            await self.s.events.emit("voice.error", error=f"Transcription failed: {exc}")
            return
        self.s.perf.record("stt_ms", latency)
        self.s.perf.record("voice_preprocess_ms", (time.perf_counter() - t0) * 1000 - latency)
        clean = text.strip()
        if not clean or re.sub(r"[^\w\s]", "", clean).strip().lower() in HALLUCINATIONS:
            await self._set_state("idle")
            await self.s.events.emit("voice.no_speech")
            return
        await self.s.events.emit("voice.transcript", text=clean, duration_s=round(duration, 2))
        await self._set_state("idle")
        info = self.orchestrator.submit(clean, self.conversation_id, source="voice")
        # Follow-ups by voice continue the same conversation.
        asyncio.create_task(self._track_conversation(info))

    async def _track_conversation(self, info: Any) -> None:
        for _ in range(50):
            if info.conversation_id:
                self.conversation_id = info.conversation_id
                return
            await asyncio.sleep(0.05)

    # ----------------------------------------------------------- wake word
    async def apply_settings(self) -> None:
        v = self.s.settings.voice
        want = v.wake_word_enabled and not self.paused
        if want and self._wake_task is None:
            self._wake_task = asyncio.create_task(self._wake_loop(), name="wake-word")
        elif not want and self._wake_task is not None:
            self._wake_task.cancel()
            try:
                await self._wake_task
            except asyncio.CancelledError:
                pass
            self._wake_task = None
        if self._wake is not None:
            self._wake.threshold = v.wake_word_threshold
        if self._mic and self._mic.device != v.input_device and self._mic.subscriber_count == 0:
            self._mic = None

    async def set_paused(self, paused: bool) -> None:
        self.paused = paused
        await self.apply_settings()
        await self._set_state(self.state)

    def _load_wake(self) -> Any:
        from voice.wakeword import WakeWordDetector

        p = self.s.paths
        files = [p.model_file("wakeword", n) for n in ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx")]
        if not all(files):
            raise FileNotFoundError("wake-word models are missing (run npm run setup)")
        return WakeWordDetector(*files, threshold=self.s.settings.voice.wake_word_threshold)  # type: ignore[arg-type]

    async def _wake_loop(self) -> None:
        try:
            if self._wake is None:
                self._wake = await asyncio.to_thread(self._load_wake)
            self._wake_error = None
        except Exception as exc:  # noqa: BLE001
            self._wake_error = str(exc)
            log.exception("wake word unavailable")
            await self.s.events.emit("voice.error", error=f"Wake word unavailable: {exc}")
            self._wake_task = None
            return
        mic = self._get_mic()
        q = mic.subscribe(maxsize=100)
        try:
            mic.open()
            await self._set_state(self.state)
            self._wake.reset()
            while True:
                frame = await q.get()
                if self._session and not self._session.done():
                    continue  # a command session owns the conversation
                score = await asyncio.to_thread(self._wake.process, frame)
                if self._wake.detected(score):
                    log.info("wake word detected (%.2f)", score)
                    await self.s.events.emit("wakeword.detected", score=round(score or 0, 3))
                    await self.speak("Yes?", task_id=None, kind="ack")
                    await asyncio.sleep(0.55)  # let the acknowledgement play
                    self._drain(q)
                    await self.start(source="wake", interrupt=False)
        except MicrophoneError as exc:
            self._wake_error = str(exc)
            self._wake_task = None  # allow apply_settings() to retry later
            await self.s.events.emit("voice.error", error=str(exc))
            await self._set_state(self.state)
        finally:
            mic.unsubscribe(q)
            self._release_mic_if_idle()

    @staticmethod
    def _drain(q: asyncio.Queue[np.ndarray]) -> None:
        while not q.empty():
            q.get_nowait()

    # ----------------------------------------------------------------- TTS
    def _prewarm_tts(self) -> None:
        """Load the Piper voice (several seconds, cold) while the user is still
        speaking, so the first spoken reply isn't delayed by model loading."""
        v = self.s.settings.voice
        model = self._piper_model()
        if v.tts_engine == "piper" and self.tts._voice is None and self.tts.available(model):  # noqa: SLF001
            asyncio.get_running_loop().run_in_executor(None, self.tts._load, model)  # noqa: SLF001

    async def speak(self, text: str, task_id: str | None, kind: str = "response") -> None:
        v = self.s.settings.voice
        if v.tts_engine == "off" or not text.strip():
            return
        model = self._piper_model()
        if v.tts_engine == "piper" and self.tts.available(model):
            try:
                clip_id, _, ms = await self.tts.synthesize(model, text, v.tts_rate)  # type: ignore[arg-type]
                self.s.perf.record("tts_ms", ms)
                await self.s.events.emit("tts.ready", clip_id=clip_id, task_id=task_id, text=text, kind=kind)
                return
            except Exception:  # noqa: BLE001
                log.exception("piper synthesis failed; falling back to system voice")
        await self.s.events.emit("tts.system", text=text, rate=v.tts_rate, task_id=task_id, kind=kind)

    async def close(self) -> None:
        await self.cancel()
        if self._wake_task:
            self._wake_task.cancel()
            try:
                await self._wake_task
            except asyncio.CancelledError:
                pass
            self._wake_task = None
        if self._mic:
            self._mic.close()
            self._mic = None
