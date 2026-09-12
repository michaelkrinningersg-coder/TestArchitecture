"""Die Anmeldung an der Oracle-Datenbank — Qt6-Fassung von ``anmeldemaske``.

Wie im Original: Benutzer, Passwort und Datenbank, einmal mit einer echten
Verbindung geprüft, danach im Arbeitsspeicher gehalten. **Das Passwort wird
nirgends gespeichert**; gemerkt werden nur Benutzername und Datenbank.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from labcontrol_qt.arbeit import im_hintergrund
from labcontrol_qt.kern import lims_db
from labcontrol_qt.quelle import DemoQuelle, LimsQuelle
from labcontrol_qt.stil import Stil, beschriftung, karte, knopf


class Anmeldemaske(QWidget):
    """Die erste Seite. Meldet mit ``angemeldet`` die fertige Quelle."""

    angemeldet = Signal(object)      # Datenquelle

    def __init__(self, konfig, demo_erlaubt: bool = True,
                 eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.konfig = konfig
        self.setStyleSheet(f"background: {Stil.BG};")

        titel = beschriftung("LabControl", groesse=20, fett=True)
        unter = beschriftung("Anmeldung an der Oracle-Datenbank der NW-FVA",
                             groesse=10, farbe=Stil.MUTED)

        self.benutzer = QLineEdit(str(konfig.get("benutzer") or ""))
        self.benutzer.setMinimumWidth(220)
        self.passwort = QLineEdit()
        self.passwort.setEchoMode(QLineEdit.Password)
        self.datenbank = QComboBox()
        self.datenbank.addItems(list(lims_db.DESKRIPTOREN))
        gemerkt = str(konfig.get("alias") or "")
        if gemerkt in lims_db.DESKRIPTOREN:
            self.datenbank.setCurrentText(gemerkt)

        formular = QFormLayout()
        formular.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        formular.setHorizontalSpacing(14)
        formular.setVerticalSpacing(10)
        formular.addRow(beschriftung("Oracle-Benutzer", groesse=9,
                                     farbe=Stil.MUTED), self.benutzer)
        formular.addRow(beschriftung("Passwort", groesse=9, farbe=Stil.MUTED),
                        self.passwort)
        formular.addRow(beschriftung("Datenbank", groesse=9, farbe=Stil.MUTED),
                        self.datenbank)

        self.knopf_anmelden = knopf("Anmelden", breite=200, hoehe=40)
        self.knopf_anmelden.clicked.connect(self.anmelden)
        self.hinweis = beschriftung("", groesse=9, farbe=Stil.ERROR, umbruch=True)
        self.hinweis.setMinimumHeight(34)

        knopfreihe = QHBoxLayout()
        knopfreihe.addWidget(self.knopf_anmelden)
        if demo_erlaubt:
            # Kein Ersatz für die Anmeldung, sondern der Weg, die Oberfläche
            # ohne VPN und ohne Datenbank anzusehen — und der Weg, auf dem
            # die Tests und die Bildschirmfotos entstehen.
            self.knopf_demo = knopf("Ohne Datenbank ansehen", hoehe=40,
                                    farbe="#e2e8f0", vordergrund=Stil.TEXT,
                                    fett=False)
            self.knopf_demo.clicked.connect(self.demo_starten)
            knopfreihe.addWidget(self.knopf_demo)

        tafel = karte()
        innen = QVBoxLayout(tafel)
        innen.setContentsMargins(26, 22, 26, 22)
        innen.setSpacing(16)
        innen.addLayout(formular)
        innen.addLayout(knopfreihe)
        innen.addWidget(self.hinweis)

        mitte = QVBoxLayout()
        mitte.addStretch(1)
        mitte.addWidget(titel, alignment=Qt.AlignHCenter)
        mitte.addWidget(unter, alignment=Qt.AlignHCenter)
        mitte.addSpacing(18)
        mitte.addWidget(tafel, alignment=Qt.AlignHCenter)
        mitte.addStretch(2)

        aussen = QHBoxLayout(self)
        aussen.addStretch(1)
        aussen.addLayout(mitte, 0)
        aussen.addStretch(1)

        self.benutzer.returnPressed.connect(self.anmelden)
        self.passwort.returnPressed.connect(self.anmelden)
        (self.passwort if self.benutzer.text() else self.benutzer).setFocus()

    # -- Aktionen -----------------------------------------------------------
    def anmelden(self) -> None:
        benutzer = self.benutzer.text().strip()
        passwort = self.passwort.text()
        alias = self.datenbank.currentText()
        self.hinweis.setText("Verbindung wird geprüft …")
        self.hinweis.setStyleSheet(f"color: {Stil.MUTED};")
        self.knopf_anmelden.setEnabled(False)

        def arbeit():
            return lims_db.anmelden(benutzer, passwort, alias)

        def fertig(zugang):
            self.knopf_anmelden.setEnabled(True)
            self.konfig.set("benutzer", benutzer)
            self.konfig.set("alias", alias)
            self.konfig.speichern()
            self.angemeldet.emit(LimsQuelle(zugang))

        def schiefgegangen(fehler: Exception):
            self.knopf_anmelden.setEnabled(True)
            self.hinweis.setStyleSheet(f"color: {Stil.ERROR};")
            self.hinweis.setText(lims_db.fehlertext(fehler))

        im_hintergrund(arbeit, fertig, schiefgegangen)

    def demo_starten(self) -> None:
        self.angemeldet.emit(DemoQuelle())
