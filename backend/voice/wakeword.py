"""Local wake-word detection using openWakeWord's ONNX models ("hey jarvis").

Implements openWakeWord's streaming feature pipeline directly on
onnxruntime (melspectrogram -> speech embedding -> classifier) to avoid the
package's heavier dependencies. Audio never leaves the machine.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

CHUNK = 1280  # 80 ms at 16 kHz


def _session(path: Path):  # noqa: ANN202
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


class WakeWordDetector:
    def __init__(self, melspec: Path, embedding: Path, classifier: Path, threshold: float = 0.5) -> None:
        self.mel = _session(melspec)
        self.emb = _session(embedding)
        self.clf = _session(classifier)
        self._mel_in = self.mel.get_inputs()[0].name
        self._emb_in = self.emb.get_inputs()[0].name
        self._clf_in = self.clf.get_inputs()[0].name
        self._clf_frames = self.clf.get_inputs()[0].shape[1] or 16
        self.threshold = threshold
        self.reset()

    def reset(self) -> None:
        self._raw = np.zeros(0, dtype=np.int16)
        self._pending = np.zeros(0, dtype=np.int16)
        self._mel_buf = np.ones((76, 32), dtype=np.float32)
        self._features = np.zeros((self._clf_frames, 96), dtype=np.float32)
        self._cooldown = 0
        self._warmup = 10  # ignore the first ~0.8 s while buffers fill

    def _melspec(self, samples: np.ndarray) -> np.ndarray:
        out = self.mel.run(None, {self._mel_in: samples.astype(np.float32)[None, :]})[0]
        return np.squeeze(out) / 10.0 + 2.0  # openWakeWord's melspec transform

    def process(self, frame: np.ndarray) -> float | None:
        """Feed int16 audio; returns the latest score when a chunk completes."""
        self._pending = np.concatenate([self._pending, frame])
        score: float | None = None
        while len(self._pending) >= CHUNK:
            chunk, self._pending = self._pending[:CHUNK], self._pending[CHUNK:]
            self._raw = np.concatenate([self._raw, chunk])[-(CHUNK + 480):]
            if len(self._raw) < CHUNK + 480:
                continue
            mel = self._melspec(self._raw)
            self._mel_buf = np.vstack([self._mel_buf, mel])[-976:]
            window = self._mel_buf[-76:]
            emb = self.emb.run(None, {self._emb_in: window[None, :, :, None].astype(np.float32)})[0]
            self._features = np.vstack([self._features, emb.reshape(1, 96)])[-self._clf_frames:]
            score = float(np.squeeze(self.clf.run(None, {self._clf_in: self._features[None].astype(np.float32)})[0]))
            if self._warmup > 0:
                self._warmup -= 1
                score = 0.0
            if self._cooldown > 0:
                self._cooldown -= 1
                score = 0.0
        return score

    def detected(self, score: float | None) -> bool:
        if score is not None and score >= self.threshold:
            self._cooldown = 25  # ~2 s refractory period
            return True
        return False
