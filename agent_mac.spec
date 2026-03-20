# -*- mode: python ; coding: utf-8 -*-
# macOS build spec for AI-LogOps Agent

a = Analysis(
    ["agent/service/entry.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("agent/config.yaml", "."),
    ],
    hiddenimports=[
        "pystray",
        "pystray._darwin",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "numpy",
        "numpy.core",
        "numpy.core.multiarray",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "win32timezone",
        "win32serviceutil",
        "win32service",
        "win32event",
        "servicemanager",
        "pywintypes",
        "pythoncom",
        "pystray._win32",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AILogOps-Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=True,  # macOS argv emulation
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AILogOps-Agent",
)
