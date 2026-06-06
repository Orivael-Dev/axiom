# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — freeze the Axiom MCP server into the `axiom-mcp` binary.
# Build (from the ax-os repo root), pointing at your Axiom main checkout:
#   AXIOM_REPO=/path/to/axiom pyinstaller --clean -y packaging/axiom-mcp.spec
import os

AXIOM = os.environ.get("AXIOM_REPO")
assert AXIOM and os.path.exists(os.path.join(AXIOM, "axiom_mcp_server.py")), \
    "set AXIOM_REPO to your Axiom main checkout"

a = Analysis(
    [os.path.join(AXIOM, "axiom_mcp_server.py")],
    pathex=[AXIOM],
    binaries=[],
    datas=[],
    hiddenimports=[
        # axiom_mcp_server lazily imports handlers — list the ones the AUI uses.
        "axiom_signing", "axiom_intent_classifier", "axiom_intent_gate", "axiom_cmaa",
        "axiom_memory_engine", "axiom_audit_ledger", "axiom_marketplace", "axiom_workspace",
        "axiom_qrf", "axiom_latent", "axiom_spec_linter",
        "axiom_os_shield", "axiom_os_shield_daemon", "axiom_cpi", "axiom_axm",
        "axiom_sovereign_phone",
        "axiom_firewall", "axiom_firewall.skill_pack", "axiom_firewall.policy",
        "axiom_event_token.bonded_pair",
        "axiom_constitutional", "axiom_constitutional.client",
        "axiom_files", "axiom_files.parser", "axiom_files.validator",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch"],  # kv_cache guards torch; the AUI tool surface doesn't need it
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="axiom-mcp",
    console=True, debug=False, strip=False, upx=True,
)
