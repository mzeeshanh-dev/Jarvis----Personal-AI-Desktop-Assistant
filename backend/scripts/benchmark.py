"""Measure the backend on this machine (no network / Groq calls).

    backend/.venv/Scripts/python.exe backend/scripts/benchmark.py

Prints JSON: backend cold start, idle memory, request latency, local tool
latency, VAD / wake-word / noise-reduction / Piper TTS costs.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402
import psutil  # noqa: E402


def ms(f, n=1):
    t = time.perf_counter()
    for _ in range(n):
        f()
    return (time.perf_counter() - t) * 1000 / n


def bench_backend() -> dict:
    data = tempfile.mkdtemp(prefix="jarvis-bench-")
    env = {**os.environ, "JARVIS_DATA_DIR": data, "JARVIS_AUTH_TOKEN": "bench", "JARVIS_RESOURCES_DIR": str(ROOT / "resources")}
    port = 8931
    t0 = time.perf_counter()
    proc = subprocess.Popen([sys.executable, str(ROOT / "backend" / "main.py"), "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    ready = None
    while time.perf_counter() - t0 < 60:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if json.load(r).get("ready"):
                    ready = (time.perf_counter() - t0) * 1000
                    break
        except OSError:
            time.sleep(0.05)
    time.sleep(3)
    # venv python.exe on Windows is a launcher; measure the whole process tree.
    root = psutil.Process(proc.pid)
    tree = [root, *root.children(recursive=True)]
    for p in tree:
        p.cpu_percent(None)
    time.sleep(5)
    idle_cpu = sum(p.cpu_percent(None) for p in tree)
    rss = sum(p.memory_info().rss for p in tree) / 1048576

    def call():
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-Jarvis-Token": "bench"})
        urllib.request.urlopen(req, timeout=5).read()
    lat = [ms(call) for _ in range(30)]
    proc.terminate()
    proc.wait(10)
    return {"cold_start_to_ready_ms": round(ready or -1), "idle_rss_mb": round(rss, 1), "idle_cpu_percent_5s": idle_cpu,
            "api_state_p50_ms": round(statistics.median(lat), 2), "api_state_p95_ms": round(sorted(lat)[28], 2)}


def bench_voice() -> dict:
    out: dict = {}
    from voice.audio import FRAME
    from voice.noise import reduce_noise
    from voice.vad import SileroVAD
    models = ROOT / "resources" / "models"
    rng = np.random.default_rng(0)
    frame = (rng.normal(0, 0.05, FRAME) * 32767).astype(np.int16)

    t = time.perf_counter()
    vad = SileroVAD(models / "vad" / "silero_vad.onnx")
    out["silero_load_ms"] = round((time.perf_counter() - t) * 1000, 1)
    out["silero_per_32ms_frame_ms"] = round(ms(lambda: vad(frame), 300), 3)

    from voice.wakeword import WakeWordDetector
    w = models / "wakeword"
    t = time.perf_counter()
    det = WakeWordDetector(w / "melspectrogram.onnx", w / "embedding_model.onnx", w / "hey_jarvis_v0.1.onnx")
    out["wakeword_load_ms"] = round((time.perf_counter() - t) * 1000, 1)
    audio = (rng.normal(0, 0.02, 16000 * 10) * 32767).astype(np.int16)
    me = psutil.Process()
    me.cpu_percent(None)
    t = time.perf_counter()
    for i in range(0, len(audio) - FRAME, FRAME):
        det.process(audio[i:i + FRAME])
    elapsed = time.perf_counter() - t
    out["wakeword_processing_ms_per_10s_audio"] = round(elapsed * 1000, 1)
    out["wakeword_realtime_cpu_estimate_percent_of_one_core"] = round(elapsed / 10 * 100, 2)

    speech = (rng.normal(0, 0.1, 16000 * 5) * 32767).astype(np.int16)
    out["noise_reduction_5s_ms"] = round(ms(lambda: reduce_noise(speech), 5), 1)

    from voice.tts import PiperTTS
    tts = PiperTTS()
    model = models / "piper" / "en_GB-alan-medium.onnx"
    t = time.perf_counter()
    tts._synth(model, "Opening Google Chrome.", 1.0)
    out["piper_first_sentence_ms_including_load"] = round((time.perf_counter() - t) * 1000)
    out["piper_short_sentence_ms"] = round(ms(lambda: tts._synth(model, "Opening Google Chrome.", 1.0), 3))
    out["piper_long_reply_ms"] = round(ms(lambda: tts._synth(model, "I found three emails about GreenBot. Two tasks are still pending: the dashboard review and the sensor calibration report.", 1.0), 2))
    return out


def bench_tools() -> dict:
    import asyncio
    from core.config import AppSettings, Paths
    from agent.tool_registry import ToolContext
    from tools import build_registry

    class Svc:
        def __init__(self):
            self.paths = Paths(data_dir=Path(tempfile.mkdtemp()), resources_dir=ROOT / "resources")
    reg = build_registry()
    s = AppSettings()
    s.permissions.workspace_roots = [str(ROOT)]
    ctx = ToolContext("b", None, s, Svc())  # type: ignore[arg-type]

    async def run(name, **args):
        t = reg.get(name)
        a = t.input_model.model_validate(args)
        st = time.perf_counter()
        await t.handler(a, ctx)
        return (time.perf_counter() - st) * 1000
    loop = asyncio.new_event_loop()
    r = {
        "list_directory_ms": round(loop.run_until_complete(run("list_directory", path=str(ROOT))), 1),
        "search_files_repo_ms": round(loop.run_until_complete(run("search_files", query="orchestrator")), 1),
        "read_file_ms": round(loop.run_until_complete(run("read_file", path=str(ROOT / "backend" / "agent" / "orchestrator.py"))), 1),
        "list_running_applications_ms": round(loop.run_until_complete(run("list_running_applications")), 1),
    }
    from tools.apps import AppIndex
    idx = AppIndex()
    r["app_index_first_resolve_ms"] = round(ms(lambda: idx.resolve("visual studio code")), 1)
    r["app_index_cached_resolve_ms"] = round(ms(lambda: idx.resolve("notepad"), 20), 2)
    loop.close()
    return r


if __name__ == "__main__":
    print(json.dumps({"machine": {"cpu": psutil.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 2**30, 1)},
                      "backend": bench_backend(), "voice": bench_voice(), "tools": bench_tools()}, indent=2))
