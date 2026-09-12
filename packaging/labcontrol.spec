# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Rezept für eine einzelne, startbare Datei.

    pyinstaller packaging/labcontrol.spec --noconfirm

Unter Windows entsteht dabei ``dist/LabControl.exe`` (64 Bit — Qt 6 gibt es
nicht mehr für 32 Bit), unter Linux eine gleichnamige ELF-Datei. Dass beides
aus demselben Rezept fällt, ist Absicht: so lässt sich das Paket auch ohne
Windows-Rechner prüfen.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"

# Qt bringt viel mit, wovon diese Anwendung nichts benutzt. Ohne diese Liste
# wandern Browser-Engine, 3D und QML mit in die Datei.
UNUSED = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtWebSockets",
    "PySide6.QtWebChannel", "PySide6.QtNfc", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    # Nichts davon gehört in eine Desktop-Anwendung dieser Größe.
    "tkinter", "flask", "jinja2", "werkzeug", "playwright", "PIL", "pytest",
    "numpy", "matplotlib", "IPython",
]

analysis = Analysis(
    [str(ROOT / "labcontrol" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "labcontrol" / "resources" / "labcontrol.png"),
            "labcontrol/resources")],
    hiddenimports=["labcontrol.main_window", "labcontrol.dialogs",
                   "labcontrol.audit_view", "labcontrol.export"],
    hookspath=[],
    runtime_hooks=[],
    excludes=UNUSED,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="LabControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # Fensteranwendung, keine Konsole im Hintergrund
    disable_windowed_traceback=False,
    icon=str(ROOT / "packaging" / "labcontrol.ico") if WINDOWS else None,
    version=str(ROOT / "packaging" / "version_info.txt") if WINDOWS else None,
)
