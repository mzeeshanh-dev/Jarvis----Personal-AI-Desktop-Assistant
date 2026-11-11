"""Voice activity detection: Silero VAD (ONNX, ~2 MB, CPU) with an energy
fallback when the model or onnxruntime is unavailable."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def _session(path: Path):  # noqa: ANN202
    import onnxruntime as ort  # lazy

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


class SileroVAD:
    name = "silero"

    def __init__(self, model_path: Path) -> None:
        self.session = _session(model_path)
        self._sr = np.array(16000, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = (frame.astype(np.float32) / 32768.0).reshape(1, -1)
        x = np.concatenate([self._context, x], axis=1)
        out, self._state = self.session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -64:]
        return float(out[0][0])


class EnergyVAD:
    """Adaptive-noise-floor energy detector (fallback)."""

    name = "energy"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._floor = -55.0

    def __call__(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
        db = 20 * np.log10(max(rms, 1e-7))
        if db < self._floor + 3:
            self._floor = 0.95 * self._floor + 0.05 * db
        return float(np.clip((db - self._floor - 6) / 12, 0, 1))


def load_vad(model_path: Path | None) -> SileroVAD | EnergyVAD:
    if model_path and model_path.exists():
        try:
            return SileroVAD(model_path)
        except Exception:  # noqa: BLE001
            log.exception("Silero VAD failed to load; using energy VAD")
    return EnergyVAD()
