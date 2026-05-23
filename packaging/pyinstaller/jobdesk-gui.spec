# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH).parents[1]
MANIFEST = ROOT / "packaging" / "windows" / "jobdesk.exe.manifest"
GUI_RESOURCES = ROOT / "src" / "jobdesk_app" / "gui" / "resources"


a = Analysis(
    [str(ROOT / "packaging" / "pyinstaller" / "jobdesk_gui_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(GUI_RESOURCES), "jobdesk_app/gui/resources")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="JobDesk",
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
    manifest=str(MANIFEST),
)
