"""Rotating file + console logging with secret redaction."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from core.secrets import RedactingFilter


def setup_logging(log_dir: Path, level: str = "INFO") -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "backend.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_h = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_h.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    redactor = RedactingFilter()
    root = logging.getLogger()
    root.handlers.clear()
    for h in (file_h, console):
        h.addFilter(redactor)
        root.addHandler(h)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "uvicorn.access", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return path
