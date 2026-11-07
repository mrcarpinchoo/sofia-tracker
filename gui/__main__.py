"""Entry point: `python -m gui [--camera N] [--no-smooth] [--maestro HOST[:PORT]]`."""

import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication

from comms.link import DEFAULT_HOST, DEFAULT_PORT
from detection import CAM_INDEX
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
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    app = QApplication(sys.argv)
    bridge = DetectionBridge(camera_index=args.camera, smoothing=not args.no_smooth)
    window = MainWindow(bridge, MaestroBridge(*args.maestro))
    window.resize(1100, 720)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
