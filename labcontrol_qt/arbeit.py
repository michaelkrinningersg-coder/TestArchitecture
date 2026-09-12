"""Arbeit, die nicht im Zeichenfaden laufen darf.

Eine Abfrage über VPN dauert Sekunden. Im Zeichenfaden ausgeführt friert
darüber das ganze Fenster ein, und der Anwender hält es für abgestürzt. Das
Original hat dafür ``im_hintergrund`` mit ``after``-Abfragen; hier tut es der
Fadenvorrat von Qt, dessen Signale von selbst im richtigen Faden ankommen.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Boten(QObject):
    fertig = Signal(object)
    schiefgegangen = Signal(Exception)


#: Laufende Aufträge, damit Python sie nicht einsammelt.
#:
#: Ohne das hält nach der Rückkehr von ``im_hintergrund`` niemand mehr eine
#: Referenz auf den Auftrag. Er wird eingesammelt, sein Signalgeber stirbt
#: mit ihm, und das Ergebnis der Abfrage kommt nie an — ohne Fehlermeldung,
#: die Oberfläche bleibt einfach leer.
_LAUFEND: set = set()


class _Auftrag(QRunnable):
    def __init__(self, arbeit: Callable[[], object]) -> None:
        super().__init__()
        # Qt löscht einen Auftrag sonst gleich nach ``run()`` — auch das
        # nimmt dem noch nicht zugestellten Signal den Absender.
        self.setAutoDelete(False)
        self.arbeit = arbeit
        self.boten = _Boten()

    @Slot()
    def run(self) -> None:
        try:
            ergebnis = self.arbeit()
        except Exception as fehler:                     # noqa: BLE001
            # Absichtlich alles: was hier durchfällt, käme sonst in einem
            # fremden Faden hoch und wäre in einer Fensteranwendung
            # unsichtbar.
            self.boten.schiefgegangen.emit(fehler)
        else:
            self.boten.fertig.emit(ergebnis)


def im_hintergrund(arbeit: Callable[[], object],
                   fertig: Callable[[object], None],
                   schiefgegangen: Callable[[Exception], None] | None = None,
                   vorrat: QThreadPool | None = None) -> None:
    """Führt ``arbeit`` nebenher aus und meldet sich im Zeichenfaden zurück."""
    auftrag = _Auftrag(arbeit)
    _LAUFEND.add(auftrag)
    auftrag.boten.fertig.connect(fertig)
    if schiefgegangen is not None:
        auftrag.boten.schiefgegangen.connect(schiefgegangen)
    # Zuletzt verbunden, also zuletzt gerufen: erst die Verarbeitung, dann
    # das Loslassen.
    auftrag.boten.fertig.connect(lambda *_: _LAUFEND.discard(auftrag))
    auftrag.boten.schiefgegangen.connect(lambda *_: _LAUFEND.discard(auftrag))
    (vorrat or QThreadPool.globalInstance()).start(auftrag)


def abwarten(zeit_ms: int = 5_000) -> bool:
    """Wartet, bis der Vorrat leer ist — für Tests und Bildschirmfotos."""
    return QThreadPool.globalInstance().waitForDone(zeit_ms)
