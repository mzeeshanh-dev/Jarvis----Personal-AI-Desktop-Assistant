"""Local text-to-speech with Piper (ONNX). Audio is served to the renderer,
which owns playback (so the UI can pause/stop/replay instantly)."""
from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import uuid
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class PiperTTS:
    def __init__(self) -> None:
        self._voice: Any = None
        self._voice_path: Path | None = None
        self._lock = asyncio.Lock()
        self._load_lock = threading.Lock()
        self.cache: OrderedDict[str, bytes] = OrderedDict()

    def available(self, model: Path | None) -> bool:
        if not model:
            return False
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return model.exists()

    def _load(self, model: Path) -> Any:
        with self._load_lock:  # pre-warm thread and synthesis may race
            return self._load_locked(model)

    def _load_locked(self, model: Path) -> Any:
        if self._voice is None or self._voice_path != model:
            from piper import PiperVoice  # lazy: loads onnxruntime + espeak data

            t = time.perf_counter()
            self._voice = PiperVoice.load(str(model))
            self._voice_path = model
            log.info("piper voice %s loaded in %.0f ms", model.name, (time.perf_counter() - t) * 1000)
        return self._voice

    def _synth(self, model: Path, text: str, rate: float) -> bytes:
        from piper import SynthesisConfig

        voice = self._load(model)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            voice.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1.0 / max(0.5, min(rate, 2.0))))
        return buf.getvalue()

    async def synthesize(self, model: Path, text: str, rate: float = 1.0) -> tuple[str, bytes, float]:
        async with self._lock:
            started = time.perf_counter()
            wav = await asyncio.to_thread(self._synth, model, text, rate)
            clip_id = uuid.uuid4().hex[:12]
            self.cache[clip_id] = wav
            while len(self.cache) > 12:
                self.cache.popitem(last=False)
            return clip_id, wav, (time.perf_counter() - started) * 1000

    def unload(self) -> None:
        self._voice = None
        self._voice_path = None
