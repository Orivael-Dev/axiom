#!/usr/bin/env python3
"""
Frozen entry for the AX OS local service (PyInstaller target).
==============================================================
Runs the FastAPI service (aui.server). When frozen and packaged as a Tauri
sidecar it:
  - points the bridge at the sibling `axiom-mcp` sidecar binary (so no Python
    is needed at runtime), via AX_OS_MCP_BIN;
  - defaults the memory / audit / marketplace ledgers to a per-user data dir;
  - creates a per-install signing key (AXIOM_MASTER_KEY) persisted there.

Dev:    python packaging/service_main.py
Frozen: ./ax-os-service            (the Tauri app spawns this)
Any explicit env you set wins over these defaults.
"""
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA", str(Path.home()))
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    d = Path(base) / "ax-os"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_sidecar(name: str) -> str | None:
    """Locate a sibling sidecar binary next to this executable (Tauri may keep
    a target-triple suffix, so match by prefix)."""
    exedir = Path(sys.executable).parent
    for p in sorted(exedir.glob(name + "*")):
        if p.is_file() and (os.name != "nt" or p.suffix.lower() in ("", ".exe")):
            return str(p)
    return None


def main() -> None:
    if getattr(sys, "frozen", False) and not os.environ.get("AX_OS_MCP_BIN"):
        mcp = _find_sidecar("axiom-mcp")
        if mcp:
            os.environ["AX_OS_MCP_BIN"] = mcp

    d = _data_dir()
    os.environ.setdefault("AXIOM_MEMORY_STORE", str(d / "memory.jsonl"))
    os.environ.setdefault("AXIOM_AUDIT_LEDGER", str(d / "audit.jsonl"))
    os.environ.setdefault("AXIOM_MARKETPLACE_LEDGER", str(d / "marketplace.jsonl"))

    if not os.environ.get("AXIOM_MASTER_KEY"):
        keyf = d / "master.key"
        if not keyf.exists():
            keyf.write_text(secrets.token_hex(32), encoding="utf-8")
        os.environ["AXIOM_MASTER_KEY"] = keyf.read_text(encoding="utf-8").strip()

    from aui.server import main as run
    run()


if __name__ == "__main__":
    main()
