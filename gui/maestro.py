"""
Qt adapter for MaestroClient: opens and closes the link on demand and turns
its callbacks (worker thread) into Qt signals (UI thread).
"""

from PySide6.QtCore import QObject, Signal

from comms.link import DEFAULT_HOST, DEFAULT_PORT
from comms.maestro import MaestroClient
from comms.protocol import Gesture


class MaestroBridge(QObject):
    info_changed = Signal(object)   # comms.maestro.LinkInfo
    rejected = Signal(str, str)     # command, reason

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self._client: MaestroClient | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None

    def open(self):
        if self._client:
            return
        self._client = MaestroClient(self.host, self.port,
                                     on_info=self.info_changed.emit,
                                     on_rejected=self.rejected.emit)
        self._client.start()

    def close(self):
        """Close the link; sends STOP first if this program controls SOFIA."""
        client, self._client = self._client, None
        if client:
            client.close()

    def arm(self):
        if self._client:
            self._client.arm()

    def stop(self):
        if self._client:
            self._client.stop()

    def gesture(self, event: str):
        """`event` as gestures.py names it (INDEX, V, PALM, ...)."""
        if self._client and event in Gesture.__members__:
            self._client.gesture(Gesture[event])

    def set_target(self, x: float, y: float):
        if self._client:
            self._client.set_target(x, y)

    def clear_target(self):
        if self._client:
            self._client.clear_target()
