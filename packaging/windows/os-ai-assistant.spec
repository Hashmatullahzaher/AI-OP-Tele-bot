# -*- mode: python ; coding: utf-8 -*-
# One-file, windowed build of the OS AI Assistant desktop app.

from pathlib import Path

root = Path(SPECPATH).parents[1]
entry = Path(SPECPATH) / "assistant_entry.py"

a = Analysis(
    [str(entry)],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Server-only integrations are not part of the desktop app.
    excludes=["tkinter", "google", "anthropic", "faster_whisper", "requests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OS-AI-Assistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
