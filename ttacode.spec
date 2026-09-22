# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the TTACode headless harness CLI.

Builds a single-file console executable ``ttacode`` (``ttacode.exe`` on
Windows) from the stdlib-only ``harness`` package. The Qt desktop app is
deliberately excluded — this binary is the headless agent only.

Build on Windows:
    .\\build\\build-exe.ps1
(or: ``pyinstaller --specpath <tmp> ttacode.spec`` inside a venv with
PyInstaller installed — keep --specpath OUT of the repo so ad-hoc
``pyinstaller`` runs never overwrite this file).

CI builds the real .exe on windows-latest; see
.github/workflows/build-exe.yml.
"""

import os

SPECPATH_ROOT = os.path.abspath(SPECPATH)  # noqa: F821  (defined by PyInstaller)

a = Analysis(  # noqa: F821
    ["harness/__main__.py"],
    pathex=[SPECPATH_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Explicitly listed so the one-file build is deterministic even
        # if a future refactor hides an import behind a helper.
        "harness",
        "harness.cli",
        "harness.agent",
        "harness.agent.loop",
        "harness.mcp",
        "harness.mcp.client",
        "harness.mcp.bridge",
        "harness.models",
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
        # The harness is stdlib-only: never bundle a GUI toolkit.
        "PySide6",
        "PyQt5",
        "PyQt6",
        "tkinter",
        "_tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "Pillow",
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
    console=True,  # console app: users run it from a terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico" if os.path.exists("assets/icon.ico") else None,
)
