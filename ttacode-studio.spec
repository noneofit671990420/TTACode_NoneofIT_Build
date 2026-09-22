# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the TalkToAi Code Studio desktop UI.

Builds a single-file *windowed* executable ``ttacode-studio``
(``ttacode-studio.exe`` on Windows) from ``studio.py`` plus the
stdlib-only ``harness`` bridge it uses for model management
(``harness.ui_bridge``: VRAM-aware model picker, resolve/tune/warm at
send time).

Unlike ``ttacode.spec`` (headless CLI), this spec must NOT exclude Qt:
PySide6 is the UI. The console spec keeps its exclusions — do not touch it.

Build on Windows:
    .\\build\\build-studio.ps1
(or: ``pyinstaller --specpath <tmp> ttacode-studio.spec`` inside a venv
with PyInstaller and PySide6==6.8.3 installed — keep --specpath OUT of
the repo so ad-hoc ``pyinstaller`` runs never overwrite this file).

CI builds the real .exe on windows-latest and attaches it to the same
GitHub Release as ttacode.exe; see .github/workflows/build-exe.yml.
"""

import os

SPECPATH_ROOT = os.path.abspath(SPECPATH)  # noqa: F821  (defined by PyInstaller)

a = Analysis(  # noqa: F821
    ["studio.py"],
    pathex=[SPECPATH_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        # First-party modules (also found by bytecode analysis; listed
        # explicitly so the one-file build stays deterministic).
        "agent_core",
        "routing",
        "ssh_tools",
        "providers",
        "desktop_inventory",
        "release_checks",
        "browser_tools",
        # Harness bridge: stdlib-only, used by studio.py for model
        # management (no Qt here — safe to bundle).
        "harness",
        "harness.cli",
        "harness.ui_bridge",
        "harness.models",
        "harness.models.loader",
        "harness.models.scanner",
        "harness.transports",
        "harness.transports.ollama",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # NOTE: PySide6/PyQt must NOT be excluded — this is the GUI build.
        "tkinter",
        "_tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ttacode-studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX trips some AV heuristics; keep the binary plain
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app: no console window on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico" if os.path.exists("assets/icon.ico") else None,
)
