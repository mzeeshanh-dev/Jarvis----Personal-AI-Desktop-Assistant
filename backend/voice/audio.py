"""Microphone capture (sounddevice / PortAudio), 16 kHz mono int16 frames.

The stream is opened only while needed (push-to-talk session or wake-word
mode) and closed otherwise, so the microphone is released when idle.
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME = 512  # 32 ms: Silero VAD window size at 16 kHz


class MicrophoneError(Exception):
    pass


def _sd() -> Any:
    try:
        import sounddevice as sd  # lazy: loads PortAudio
    except OSError as exc:  # PortAudio missing
        raise MicrophoneError(f"Audio subsystem unavailable: {exc}") from exc
    return sd


def list_input_devices() -> dict[str, Any]:
    sd = _sd()
    devices = []
    hostapis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            devices.append({"index": i, "name": d["name"], "hostapi": hostapis[d["hostapi"]]["name"],
                            "default_samplerate": d["default_samplerate"]})
    try:
        default = sd.default.device[0]
    except Exception:  # noqa: BLE001
        default = None
    # Prefer MME entries (they resample to 16 kHz reliably) and de-duplicate names.
    seen: set[str] = set()
    unique = []
    for d in sorted(devices, key=lambda d: d["hostapi"] != "MME"):
        key = d["name"][:31]
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return {"devices": unique, "default": default}


class MicrophoneStream:
    """Pushes 512-sample int16 frames to subscriber queues on the event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, device: int | None = None) -> None:
        self.loop = loop
        self.device = device
        self._stream: Any = None
        self._subs: list[asyncio.Queue[np.ndarray]] = []
        self._ratio = 1.0
        self._resample_buf = np.zeros(0, dtype=np.float32)
        self._out_buf = np.zeros(0, dtype=np.int16)

    @property
    def active(self) -> bool:
        return self._stream is not None

    def subscribe(self, maxsize: int = 400) -> asyncio.Queue[np.ndarray]:
        q: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=maxsize)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[np.ndarray]) -> None:
        if q in self._subs:
            self._subs.remove(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def open(self) -> None:
        if self._stream is not None:
            return
        sd = _sd()
        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME,
                                          device=self.device, callback=self._callback)
            self._ratio = 1.0
        except Exception:  # noqa: BLE001 - device can't do 16 kHz: capture native and resample
            info = sd.query_devices(self.device, "input")
            native = int(info["default_samplerate"])
            self._ratio = native / SAMPLE_RATE
            try:
                self._stream = sd.InputStream(samplerate=native, channels=1, dtype="int16",
                                              blocksize=int(FRAME * self._ratio), device=self.device, callback=self._callback)
            except Exception as exc:  # noqa: BLE001
                self._stream = None
                raise MicrophoneError(f"Could not open microphone: {exc}. Check Windows Settings → Privacy → Microphone.") from exc
        self._stream.start()
        log.info("microphone opened (device=%s, ratio=%.2f)", self.device, self._ratio)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
                self._out_buf = np.zeros(0, dtype=np.int16)
                log.info("microphone released")

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:  # PortAudio thread
        data = indata[:, 0].copy()
        if self._ratio != 1.0:
            n_out = int(round(len(data) / self._ratio))
            x = np.linspace(0, len(data) - 1, n_out)
            data = np.interp(x, np.arange(len(data)), data.astype(np.float32)).astype(np.int16)
        self.loop.call_soon_threadsafe(self._dispatch, data)

    def _dispatch(self, data: np.ndarray) -> None:
        self._out_buf = np.concatenate([self._out_buf, data])
        while len(self._out_buf) >= FRAME:
            frame, self._out_buf = self._out_buf[:FRAME], self._out_buf[FRAME:]
            for q in self._subs:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(frame)


def rms_level(frame: np.ndarray) -> float:
    """0..1 loudness for the UI meter."""
    if frame.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
    db = 20 * np.log10(max(rms, 1e-6))
    return float(np.clip((db + 60) / 50, 0, 1))


def to_wav(samples: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


async def check_microphone(device: int | None, seconds: float = 0.6) -> dict[str, Any]:
    """Open the mic briefly; detects missing permission (all-zero input)."""
    loop = asyncio.get_running_loop()
    mic = MicrophoneStream(loop, device)
    q = mic.subscribe()
    try:
        mic.open()
    except MicrophoneError as exc:
        return {"ok": False, "error": str(exc)}
    frames = []
    try:
        end = loop.time() + seconds
        while loop.time() < end:
            try:
                frames.append(await asyncio.wait_for(q.get(), 1.0))
            except asyncio.TimeoutError:
                break
    finally:
        mic.close()
    if not frames:
        return {"ok": False, "error": "No audio received from the microphone."}
    audio = np.concatenate(frames)
    peak = int(np.max(np.abs(audio)))
    if peak == 0:
        return {"ok": False, "error": "Microphone returns silence. Windows may be blocking microphone access for desktop apps "
                                      "(Settings → Privacy & security → Microphone).", "peak": 0}
    return {"ok": True, "peak": peak, "level": rms_level(audio)}
