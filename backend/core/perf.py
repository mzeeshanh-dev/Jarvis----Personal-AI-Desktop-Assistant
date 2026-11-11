"""Performance metrics. Computed on request only (no background polling)."""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Any

import psutil


class PerfMonitor:
    def __init__(self) -> None:
        self.started = time.time()
        self._samples: dict[str, deque[float]] = {}
        self._proc = psutil.Process(os.getpid())
        self._proc.cpu_percent(None)  # prime the counter
        self.startup_ms: float | None = None

    def record(self, name: str, ms: float) -> None:
        self._samples.setdefault(name, deque(maxlen=200)).append(float(ms))

    def stats(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for name, dq in self._samples.items():
            if not dq:
                continue
            vals = sorted(dq)
            n = len(vals)
            out[name] = {"count": n, "avg": round(sum(vals) / n, 1), "p50": round(vals[n // 2], 1),
                         "p95": round(vals[min(n - 1, int(n * 0.95))], 1), "last": round(dq[-1], 1)}
        return out

    def snapshot(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        p = self._proc
        with p.oneshot():
            mem = p.memory_info().rss
            cpu = p.cpu_percent(None)
            threads = p.num_threads()
        children = []
        for c in p.children(recursive=True):
            try:
                children.append({"pid": c.pid, "name": c.name(), "memory_mb": round(c.memory_info().rss / 1048576, 1)})
            except psutil.Error:
                continue
        vm = psutil.virtual_memory()
        return {
            "backend": {"pid": p.pid, "cpu_percent": cpu, "memory_mb": round(mem / 1048576, 1), "threads": threads,
                        "uptime_s": round(time.time() - self.started), "startup_ms": self.startup_ms},
            "children": children,
            "system": {"cpu_percent": psutil.cpu_percent(None), "memory_percent": vm.percent,
                       "memory_available_mb": round(vm.available / 1048576)},
            "latency": self.stats(),
            **(extra or {}),
        }
