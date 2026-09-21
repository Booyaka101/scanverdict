# PyInstaller spec for the single-file scanverdict.exe.
#
#     pip install pyinstaller
#     pyinstaller scanverdict.spec
#
# ffmpeg and ffprobe are deliberately not bundled. They are the user's own
# install, they are large, and redistributing a build means taking on its
# licensing. scanverdict prints an install hint when it cannot find them.

a = Analysis(
    ["packaging/exe_entry.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="scanverdict",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
