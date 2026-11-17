# Separate onedir GUI build. PyInstaller's PySide6 hooks collect Qt plugins.
from pathlib import Path

project_root = Path(SPECPATH).resolve()

a = Analysis(
    [str(project_root / "run_client.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide2", "PyQt5", "PyQt6"],
    noarchive=False,
)
# Qt uses the ICU implementation shipped by Windows. PyInstaller otherwise
# picks up an unrelated icuuc.dll from the build host's PATH (for example,
# another application's private ICU build), which can break Qt DLL loading.
a.binaries = [
    entry for entry in a.binaries
    if entry[0].lower() not in {"icuuc.dll", "icudt78.dll"}
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WhiteboardClient",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WhiteboardClient",
    contents_directory=".",
)
