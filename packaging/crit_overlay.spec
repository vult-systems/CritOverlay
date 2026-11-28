# PyInstaller spec for the standalone build. Run through packaging/build.py
# (via packaging\build.bat), which pins the toolchain, writes
# build/build_info.json, and verifies the result. Directly:
#     pyinstaller packaging/crit_overlay.spec
#
# Onedir rather than onefile, because onefile re-extracts its whole bundle
# to %TEMP% on every launch. Besides being slow, that breaks on machines
# where policy blocks executing from %TEMP% (common AppLocker/WDAC rule),
# and antivirus scans the freshly extracted DLLs every time.
#
# Two executables share one _internal folder:
#   CritOverlay.exe          the app (no console window)
#   CritOverlay-console.exe  same app with a console, for --selftest,
#                            --diagnose and --debug output
#
# QT_EXCLUDES is Qt submodules the app never imports (it only uses QtCore,
# QtGui and QtWidgets). If you change the list, rebuild and run the
# checklist in docs/COMPATIBILITY.md; packaging/verify_build.py checks the
# bundle's DLL dependencies statically.

import json
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

block_cipher = None
root = Path(SPECPATH).resolve().parent

_constants = (root / "crit_overlay" / "constants.py").read_text(encoding="utf-8")
APP_VERSION = re.search(r'^APP_VERSION = "([^"]+)"', _constants, re.M).group(1)
_v = [int(x) for x in re.findall(r"\d+", APP_VERSION)[:3]] + [0]
_v = tuple((_v + [0, 0, 0, 0])[:4])

QT_EXCLUDES = [
    "PySide6.QtNetwork",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtWebSockets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtXml",
    "PySide6.QtStateMachine",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSpatialAudio",
    "PySide6.QtGraphs",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    # Never imported; keeps the analysis from dragging them in through
    # stdlib modules that mention them.
    "tkinter",
    "_tkinter",
]

# Fonts for the text tool, with their licenses (SIL OFL 1.1).
datas = [(str(root / "crit_overlay" / "fonts"), "crit_overlay/fonts")]
# Optional libmypaint comparison engine (exp/shape-tools prototype), staged by
# third_party/libmypaint/build_libmypaint.py. Shipped only when built.
if (root / "crit_overlay" / "mypaint" / "mypaint.dll").exists():
    datas.append((str(root / "crit_overlay" / "mypaint"), "crit_overlay/mypaint"))
_build_info = root / "build" / "build_info.json"
if _build_info.exists():
    datas.append((str(_build_info), "."))

a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=QT_EXCLUDES,
    cipher=block_cipher,
    noarchive=False,
)

# QT_EXCLUDES only drops the Python binding (.pyd) for each module. The
# PySide6 hook still pulls in some native Qt DLLs and plugins, so strip those
# by name. The UI is plain QWidgets with QPainter (CPU raster rendering), so
# none of these are reachable.
_STRIP_BINARIES = [
    # Software-GL fallback renderer, ~20MB, only used by QOpenGLWidget /
    # Qt Quick. Nothing here creates an OpenGL context.
    "opengl32sw.dll",
    # QML / Qt Quick.
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmodels.dll",
    "qt6qmlmeta.dll",
    "qt6qmlworkerscript.dll",
    "qt6pdf.dll",
    # On-screen keyboard.
    "qt6virtualkeyboard.dll",
    "qtvirtualkeyboardplugin.dll",
    # TUIO touch-over-UDP input plugin, and Qt6Network which only it uses.
    # The app does no networking; shipping a network-listening plugin just
    # draws attention from endpoint security tools.
    "qtuiotouchplugin.dll",
    "qt6network.dll",
]
a.binaries = [b for b in a.binaries if not any(p in b[0].lower().replace("\\", "/").split("/")[-1] for p in _STRIP_BINARIES)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_v, prodvers=_v, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [
                StringStruct("CompanyName", "CritOverlay"),
                StringStruct("FileDescription", "Crit Overlay drawing overlay"),
                StringStruct("FileVersion", APP_VERSION),
                StringStruct("InternalName", "CritOverlay"),
                StringStruct("LegalCopyright", "MIT License"),
                StringStruct("OriginalFilename", "CritOverlay.exe"),
                StringStruct("ProductName", "Crit Overlay"),
                StringStruct("ProductVersion", APP_VERSION),
            ])
        ]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)


def _exe(name, console):
    return EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        # UPX-packed executables are a classic antivirus false positive.
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        # asInvoker: never requests elevation.
        uac_admin=False,
        icon=str(root / "assets" / "icon.ico"),
        version=version_info,
    )


app_exe = _exe("CritOverlay", console=False)
console_exe = _exe("CritOverlay-console", console=True)

coll = COLLECT(
    app_exe,
    console_exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CritOverlay",
)
