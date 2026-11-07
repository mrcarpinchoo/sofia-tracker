"""Entry point: `python -m gui [--camera N] [--no-smooth] [--maestro HOST[:PORT]]
[--log-dir DIR | --no-log]`."""

import argparse
import logging
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtWidgets import QApplication

from comms.link import DEFAULT_HOST, DEFAULT_PORT
from detection import CAM_INDEX
from events import LOG_DIR, Event, EventLog, Level
from gui.bridge import DetectionBridge
from gui.maestro import MaestroBridge
from gui.main_window import MainWindow


def address(text: str) -> tuple[str, int]:
    host, _, port = text.partition(":")
    return host, int(port) if port else DEFAULT_PORT


def parse_args():
    parser = argparse.ArgumentParser(description="SOFIA control GUI")
    parser.add_argument(
        "--camera", type=int, default=CAM_INDEX,
        help="Initial camera index (can be changed in the window)",
    )
    parser.add_argument(
        "--no-smooth", action="store_true",
        help="Start with smoothing disabled",
    )
    parser.add_argument(
        "--maestro", type=address, default=(DEFAULT_HOST, DEFAULT_PORT),
        metavar="HOST[:PORT]",
        help=f"SOFIA Maestro address (default {DEFAULT_HOST}:{DEFAULT_PORT})",
    )
    log = parser.add_mutually_exclusive_group()
    log.add_argument(
        "--log-dir", type=Path, default=LOG_DIR,
        help="Folder for the session's event log, a CSV (default: logs/)",
    )
    log.add_argument(
        "--no-log", action="store_true",
        help="Don't write an event log",
    )
    return parser.parse_args()


def install_exception_hooks(window: MainWindow):
    """Log exceptions nobody caught, instead of losing them or the program.

    An exception in a Qt slot or a worker callback ends up here; the
    application keeps running. Events reach the window through a queued
    call, so this is safe from any thread.
    """
    def report(exc_type, exc, tb, where: str):
        last = traceback.extract_tb(tb)[-1] if tb else None
        place = f" en {Path(last.filename).name}:{last.lineno}" if last else ""
        window.record_from_any_thread(Event(
            Level.ERROR, "gui", "EXCEPCION_NO_CONTROLADA",
            f"{exc_type.__name__}: {exc}{place} ({where})",
            "se registra y la aplicación sigue en ejecución",
        ))

    def excepthook(exc_type, exc, tb):
        sys.__excepthook__(exc_type, exc, tb)
        report(exc_type, exc, tb, "hilo principal")

    def thread_excepthook(args: threading.ExceptHookArgs):
        threading.__excepthook__(args)
        name = args.thread.name if args.thread else "?"
        report(args.exc_type, args.exc_value, args.exc_traceback, f"hilo {name}")

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    event_log = EventLog(None if args.no_log else args.log_dir)
    if event_log.path:
        logging.info("Event log: %s", event_log.path)

    app = QApplication(sys.argv)
    bridge = DetectionBridge(camera_index=args.camera, smoothing=not args.no_smooth)
    window = MainWindow(bridge, MaestroBridge(*args.maestro), event_log)
    install_exception_hooks(window)
    window.resize(1100, 720)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
