# PyInstaller spec for a standalone, windowed (no console) build. Run from
# the repo root: pyinstaller packaging/crit_overlay.spec
#
# Onedir rather than onefile, because onefile re-extracts its whole bundle
# to a temp directory on every launch and this app gets launched a lot.
#
# QT_EXCLUDES is Qt submodules the app never imports (it only uses QtCore,
# QtGui and QtWidgets) that PySide6's default hook bundles anyway. If you
# change the list, launch the build and click through every feature.

from pathlib import Path

block_cipher = None
root = Path(SPECPATH).resolve().parent

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
]

a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=QT_EXCLUDES,
    cipher=block_cipher,
    noarchive=False,
)

# QT_EXCLUDES only drops the Python binding (.pyd) for each module. The
# PySide6 hook still pulls in the underlying native Qt DLLs, so strip those
# by name. The UI is plain QWidgets with QPainter, so none of these are
# reachable.
_STRIP_BINARIES = [
    # Software-GL fallback renderer, ~20MB, only used by QOpenGLWidget.
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
]
a.binaries = [b for b in a.binaries if not any(p in b[0].lower() for p in _STRIP_BINARIES)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CritOverlay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(root / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CritOverlay",
)
