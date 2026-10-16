"""
Qt adapter for DetectionWorker.

The worker calls back on its own thread; this bridge turns those callbacks
into Qt signals, which Qt delivers on the UI thread. Widgets connect to the
signals and never touch the worker thread directly.
"""

import threading

from PySide6.QtCore import QObject, Signal, Slot

from detection import CAM_INDEX, DetectionWorker, FrameResult, WorkerState


class DetectionBridge(QObject):
    """Starts, pauses, resumes and stops detection; reports via signals.

    Frames are delivered latest-only: if the UI hasn't drawn the previous
    frame yet, the newer one replaces it instead of piling up in the event
    queue. Gesture events are never dropped.
    """

    frame_ready = Signal(object)            # FrameResult
    state_changed = Signal(object)          # WorkerState
    error = Signal(str)
    gesture_detected = Signal(str, float)   # event, time.monotonic() timestamp

    _frame_available = Signal()             # internal: worker -> UI thread

    def __init__(self, camera_index: int = CAM_INDEX, smoothing: bool = True,
                 parent: QObject | None = None):
        super().__init__(parent)
        # Applied on the next start()
        self.camera_index = camera_index
        self.smoothing = smoothing

        self._worker: DetectionWorker | None = None
        self._frame_lock = threading.Lock()
        self._latest_frame: FrameResult | None = None
        self._frame_available.connect(self._deliver_frame)

    @property
    def state(self) -> WorkerState:
        return self._worker.state if self._worker else WorkerState.STOPPED

    def start(self):
        """Open the camera and detect, using the current settings."""
        if self.state is not WorkerState.STOPPED:
            return
        self._worker = DetectionWorker(
            self._on_frame,
            camera_index=self.camera_index,
            smoothing=self.smoothing,
            on_state_change=self.state_changed.emit,
            on_error=lambda exc: self.error.emit(str(exc)),
        )
        self._worker.start()

    def pause(self):
        if self._worker:
            self._worker.pause()

    def resume(self):
        if self._worker:
            self._worker.resume()

    def stop(self):
        """Ask the worker to stop without blocking the UI.

        state_changed reports STOPPED once the camera is released.
        """
        if self._worker:
            self._worker.stop(timeout=0)

    def shutdown(self):
        """Stop and wait for the worker (use when the app is closing)."""
        if self._worker:
            self._worker.stop()

    # Worker thread ---------------------------------------------------------
    def _on_frame(self, result: FrameResult):
        detection = result.detection
        if detection and detection.event:
            self.gesture_detected.emit(detection.event, detection.timestamp)

        with self._frame_lock:
            already_queued = self._latest_frame is not None
            self._latest_frame = result
        if not already_queued:
            self._frame_available.emit()

    # UI thread -------------------------------------------------------------
    @Slot()
    def _deliver_frame(self):
        with self._frame_lock:
            result, self._latest_frame = self._latest_frame, None
        if result is not None:
            self.frame_ready.emit(result)
