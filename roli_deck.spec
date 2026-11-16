# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build:  pyinstaller roli_deck.spec --noconfirm
# Produces dist/ROLI-Deck/ROLI-Deck.exe (one-dir build, windowed).
#
# bleak's Windows backend loads the winrt-* projection packages dynamically, and
# pystray picks its Win32 backend at runtime, so both are collected explicitly.

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = (
    collect_submodules("winrt")
    + collect_submodules("bleak")
    + ["pystray._win32", "PIL.ImageDraw", "rtmidi"]
)
datas = collect_data_files("winrt", include_py_files=False) + collect_data_files("bleak")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ROLI-Deck",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ROLI-Deck")
