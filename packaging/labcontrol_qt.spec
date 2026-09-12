# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Rezept der Qt6-Portierung.

    pyinstaller packaging/labcontrol_qt.spec --noconfirm

Ergebnis ist eine einzelne Datei. Unter Windows zwangsläufig 64-bit — Qt 6
gibt es nicht für 32 Bit, und was das für den Oracle-Client bedeutet, steht
in docs/portierung.md.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
KERN = ROOT / "labcontrol_qt" / "kern"
WINDOWS = sys.platform == "win32"

# Die Fachschicht importiert einander unter blanken Namen, so wie sie im
# flachen Ursprungs-Repo liegt. PyInstaller findet sie deshalb nur, wenn ihr
# Verzeichnis im Suchpfad steht und die Namen ausdrücklich genannt sind.
FACHSCHICHT = ["config", "dateien", "laufkontext", "lims_db", "protokoll",
               "sitzung", "verschleppung"]

UNUSED = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtCharts",
    "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtDesigner",
    "PySide6.QtOpenGL", "PySide6.QtTest", "PySide6.QtBluetooth",
    "PySide6.QtSerialPort", "PySide6.QtWebSockets", "PySide6.QtSensors",
    "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio",
    "tkinter", "tkinterdnd2", "flask", "jinja2", "werkzeug", "playwright",
    "pytest", "matplotlib", "IPython",
]

analysis = Analysis(
    [str(ROOT / "labcontrol_qt" / "__main__.py")],
    pathex=[str(ROOT), str(KERN)],
    binaries=[],
    datas=[(str(KERN / name), "labcontrol_qt/kern")
           for name in ("Icon.ico", "Icon.png") if (KERN / name).exists()],
    hiddenimports=FACHSCHICHT + [
        "labcontrol_qt.hauptfenster", "labcontrol_qt.messfenster",
        "labcontrol_qt.anmeldung", "labcontrol_qt.quelle",
        "labcontrol_qt.raster", "labcontrol_qt.stil", "labcontrol_qt.arbeit",
    ],
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
    name="LabControl-Qt6",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    icon=str(KERN / "Icon.ico") if WINDOWS and (KERN / "Icon.ico").exists() else None,
)
