# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ["agent/service/win_service.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("agent/config.yaml", "."),
    ],
    hiddenimports=[
        "win32timezone",
        "win32serviceutil",
        "win32service",
        "win32event",
        "servicemanager",
        "pywintypes",
        "pythoncom",
        "pystray",
        "pystray._win32",
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
    excludes=[],
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
    argv_emulation=False,
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
