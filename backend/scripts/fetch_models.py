"""Download the small voice models bundled with Jarvis into resources/models.

Run via `npm run setup` (or directly with the backend venv's Python). Files that
already exist are skipped, so this is safe to re-run.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "resources" / "models"

OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium"

FILES: dict[str, str] = {
    "vad/silero_vad.onnx": "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
    "wakeword/melspectrogram.onnx": f"{OWW}/melspectrogram.onnx",
    "wakeword/embedding_model.onnx": f"{OWW}/embedding_model.onnx",
    "wakeword/hey_jarvis_v0.1.onnx": f"{OWW}/hey_jarvis_v0.1.onnx",
    "piper/en_GB-alan-medium.onnx": f"{PIPER}/en_GB-alan-medium.onnx",
    "piper/en_GB-alan-medium.onnx.json": f"{PIPER}/en_GB-alan-medium.onnx.json",
}


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-setup"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
        while chunk := resp.read(1 << 16):
            fh.write(chunk)
    tmp.replace(dest)


def main() -> int:
    failed = 0
    for rel, url in FILES.items():
        dest = MODELS / rel
        if dest.exists() and dest.stat().st_size > 0:
            print(f"[models] exists  {rel}")
            continue
        try:
            print(f"[models] fetch   {rel}")
            download(url, dest)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"[models] FAILED  {rel}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
