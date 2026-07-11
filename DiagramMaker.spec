# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('C:\\Users\\liram\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\DLLs\\tcl86t.dll', '.'), ('C:\\Users\\liram\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\DLLs\\tk86t.dll', '.')],
    datas=[('style.css', '.'), ('icon.ico', '.'), ('ui/Icons', 'ui/Icons'), ('C:\\Users\\liram\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\tcl\\tcl8.6', '_tcl_data'), ('C:\\Users\\liram\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\tcl\\tk8.6', '_tk_data')],
    hiddenimports=[],
    hookspath=['pyinstaller_hooks'],
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
    a.binaries,
    a.datas,
    [],
    name='DiagramMaker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
