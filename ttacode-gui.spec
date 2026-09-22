# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the TTACode desktop chat GUI.

Builds a single-file *windowed* executable ``ttacode`` (``ttacode.exe``
on Windows) from ``harness/gui/app.py``. This is the average-user
launcher: double-click, warm model, chat — no flags, no terminal.
PySide6 is the UI, so unlike ``ttacode.spec`` (headless CLI) this spec
must NOT exclude Qt.

The headless CLI ships separately as ``ttacode-cli.exe`` (console).

Build on Windows:
    .\\build\\build-gui.ps1
(or: ``pyinstaller --specpath <tmp> ttacode-gui.spec`` inside a venv
with PyInstaller and PySide6==6.8.3 installed — keep --specpath OUT of
the repo so ad-hoc ``pyinstaller`` runs never overwrite this file).

CI builds the real .exe on windows-latest and attaches it to the same
GitHub Release as ttacode-cli.exe; see .github/workflows/build-exe.yml.
"""

import os

SPECPATH_ROOT = os.path.abspath(SPECPATH)  # noqa: F821  (defined by PyInstaller)

a = Analysis(  # noqa: F821
    ["harness/gui/app.py"],
    pathex=[SPECPATH_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        # GUI package (also found by bytecode analysis; listed
        # explicitly so the one-file build stays deterministic).
        "harness.gui",
        "harness.gui.app",
        "harness.gui.main_window",
        "harness.gui.worker",
        "harness.gui.theme",
        "harness.gui.markdown",
        "harness.gui.images",
        "harness.gui.models",
        # Shared session path: config, model load, tools, agent loop.
        "harness",
        "harness.cli",
        "harness.session",
        "harness.agent",
        "harness.agent.loop",
        "harness.mcp",
        "harness.mcp.client",
        "harness.mcp.bridge",
        "harness.models",
        "harness.models.loader",
        "harness.models.scanner",
        "harness.skills",
        "harness.skills.loader",
        "harness.tools",
        "harness.tools.registry",
        "harness.tools.schema",
        "harness.tools.context",
        "harness.tools.plugins",
        "harness.tools.builtin",
        "harness.tools.builtin.files",
        "harness.tools.builtin.shell",
        "harness.tools.builtin.web",
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
    name="ttacode",
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
