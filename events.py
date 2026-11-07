"""
SOFIA - Event log
-----------------
Errors, recoveries and commands, reported by the camera worker, the Maestro
client and the GUI, and written to a CSV file (one per session) as evidence
for the tests: when each problem happened, under which conditions, and what
the program did about it.

Columns: fecha_hora, nivel, origen, evento, detalle, accion, contexto.
Every row is flushed at once, so the file survives a crash. A file that
can't be written disables the log instead of stopping the program.
"""

import csv
import enum
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

LOG_DIR = Path(__file__).with_name("logs")
COLUMNS = ("fecha_hora", "nivel", "origen", "evento", "detalle", "accion", "contexto")


class Level(enum.Enum):
    INFO = "INFO"
    WARNING = "AVISO"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Event:
    level: Level
    source: str          # "camara", "maestro", "gui"
    name: str            # e.g. CAMARA_PERDIDA, ENLACE_RESTABLECIDO
    detail: str = ""     # what happened
    action: str = ""     # what the program did about it

    def __str__(self) -> str:
        text = f"{self.name}: {self.detail}" if self.detail else self.name
        return f"{text} → {self.action}" if self.action else text


class EventLog:
    """Appends events to `<directory>/eventos_<date>_<time>.csv`.

    Thread-safe. With `directory` None nothing is written.
    """

    def __init__(self, directory: Path | None = LOG_DIR):
        self._lock = threading.Lock()
        self._file = None
        self._writer = None
        self.path: Path | None = None
        if directory is None:
            return
        path = Path(directory) / f"eventos_{datetime.now():%Y%m%d_%H%M%S}.csv"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # utf-8-sig so Excel shows the accents
            self._file = path.open("w", newline="", encoding="utf-8-sig")
            self._writer = csv.writer(self._file)
            self._writer.writerow(COLUMNS)
            self._file.flush()
            self.path = path
        except OSError as exc:
            log.warning("Event log disabled, can't write %s: %s", path, exc)
            self._file = self._writer = None

    def record(self, event: Event, context: str = ""):
        with self._lock:
            if self._writer is None:
                return
            try:
                self._writer.writerow((
                    datetime.now().isoformat(sep=" ", timespec="milliseconds"),
                    event.level.value, event.source, event.name,
                    event.detail, event.action, context,
                ))
                self._file.flush()
            except OSError as exc:
                log.warning("Event log disabled after a write error: %s", exc)
                self._close()

    def close(self):
        with self._lock:
            self._close()

    def _close(self):
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass
        self._file = self._writer = None
