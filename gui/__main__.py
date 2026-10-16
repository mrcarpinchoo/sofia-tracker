"""Entry point: `python -m gui [--camera N] [--no-smooth]`."""

import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication

from detection import CAM_INDEX
from gui.bridge import DetectionBridge
from gui.main_window import MainWindow


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
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    app = QApplication(sys.argv)
    bridge = DetectionBridge(camera_index=args.camera, smoothing=not args.no_smooth)
    window = MainWindow(bridge)
    window.resize(1100, 720)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
