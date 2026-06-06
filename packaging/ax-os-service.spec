# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — freeze the AX OS local service into `ax-os-service`.
# Build from the ax-os repo root:  pyinstaller --clean -y packaging/ax-os-service.spec
import os

ROOT = os.path.abspath(os.getcwd())

a = Analysis(
    ["packaging/service_main.py"],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        "uvicorn", "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
        "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan", "uvicorn.lifespan.on",
        "fastapi", "pydantic",
        "aui", "aui.server", "aui.plan", "aui.planner_claude", "aui.panels",
        "bridge", "bridge.client",
        "workspace", "workspace.assembler", "workspace.branch",
        "marketplace", "marketplace.store", "marketplace.runner",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="ax-os-service",
    console=True, debug=False, strip=False, upx=True,
)
