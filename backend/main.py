"""Jarvis backend entry point. Normally launched by Electron as a child process.

    python main.py --port 51234        (dev)
    jarvis-backend.exe --port 51234    (packaged)

Environment (set by Electron): JARVIS_AUTH_TOKEN, JARVIS_DATA_DIR,
JARVIS_RESOURCES_DIR, JARVIS_ALLOWED_ORIGINS, JARVIS_PARENT_PID.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import threading
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

EXIT_PORT_IN_USE = 98


def _watch_parent(pid: int, on_exit) -> None:  # noqa: ANN001
    """Block in a thread on the parent's process handle (no polling); exit
    if Electron dies so the backend is never orphaned."""
    import psutil

    def run() -> None:
        try:
            psutil.Process(pid).wait()
        except psutil.Error:
            pass
        on_exit()
    threading.Thread(target=run, name="parent-watch", daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("JARVIS_PORT", "8765")))
    parser.add_argument("--log-level", default=os.environ.get("JARVIS_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("refusing to bind to a non-loopback address", file=sys.stderr)
        return 2

    # Fail fast with a distinct exit code so Electron can retry another port.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((args.host, args.port))
    except OSError:
        print(f"port {args.port} in use", file=sys.stderr)
        return EXIT_PORT_IN_USE
    finally:
        probe.close()

    from core.config import Paths
    from core.logging_setup import setup_logging

    paths = Paths()
    paths.ensure()
    setup_logging(paths.logs, args.log_level)

    import uvicorn

    from core.app import create_app

    app = create_app(paths=paths)
    config = uvicorn.Config(app, host=args.host, port=args.port, log_config=None, access_log=False,
                            ws="websockets-sansio", lifespan="on", ws_max_size=4 * 1024 * 1024)
    server = uvicorn.Server(config)
    app.state.server = server

    parent = os.environ.get("JARVIS_PARENT_PID")
    if parent and parent.isdigit():
        _watch_parent(int(parent), lambda: setattr(server, "should_exit", True))

    logging.getLogger(__name__).info("starting backend on %s:%s", args.host, args.port)
    # asyncio.run uses the Proactor loop on Windows (needed for subprocesses/Playwright).
    asyncio.run(server.serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
