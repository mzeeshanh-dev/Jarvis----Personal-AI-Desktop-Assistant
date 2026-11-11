"""Add a Groq API key from a file to Jarvis (Windows Credential Manager + DB row).

    backend/.venv/Scripts/python.exe backend/scripts/seed_groq_key.py <key-file> [--label NAME] [--scope SCOPE]

The key is never printed. Run it while Jarvis is closed (it writes the same
SQLite database the app uses, in %APPDATA%\\Jarvis by default).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import Paths  # noqa: E402
from core.events import EventBus  # noqa: E402
from core.secrets import SecretStore, key_hint  # noqa: E402
from providers.manager import ProviderManager  # noqa: E402
from storage.database import Database  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key_file")
    ap.add_argument("--label", default="Primary")
    ap.add_argument("--scope", default="default")
    args = ap.parse_args()
    key = Path(args.key_file).read_text(encoding="utf-8").strip()
    if not key.startswith("gsk_"):
        print("file does not contain a Groq key (expected gsk_…)")
        return 2
    paths = Paths()
    paths.ensure()
    db = Database(paths.database)
    await db.open()
    secrets = SecretStore()
    mgr = ProviderManager(db, secrets, EventBus())
    await mgr.load()
    for p in mgr.providers.values():
        if p.key_hint == key_hint(key) and await secrets.get(p.credential_ref) == key:
            print(f"already configured as '{p.label}' ({p.key_hint}); re-testing")
            result = await mgr.test(p.id)
            break
    else:
        p = await mgr.add(args.label, key, args.scope)
        print(f"added '{p.label}' ({p.key_hint}) scope={p.quota_scope} -> Windows Credential Manager, data dir {paths.data}")
        result = await mgr.test(p.id)
    if result["ok"]:
        print(f"test OK: {result['latency_ms']} ms, {len(result['models'])} models available")
    else:
        print(f"test FAILED: {result['error']}")
    await mgr.close()
    await db.close()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
