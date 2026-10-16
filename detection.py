"""
SOFIA - Detection pipeline
--------------------------
Everything between the webcam and the gesture events, independent of any
front end:

  Camera             opens the webcam and returns mirrored BGR frames
  DetectionPipeline  one frame in, one Detection out (MediaPipe + gestures)
  draw_detection     draws the hand skeleton and palm centroid on a frame
  DetectionWorker    runs Camera + DetectionPipeline on a background thread
                     that can be started, paused, resumed and stopped

tracker.py (CLI) drives Camera and DetectionPipeline from its own loop.
The GUI uses DetectionWorker so that video processing never
blocks the interface.

Note: mediapipe deprecated the old `mp.solutions.hands` API back in 2023,
and recent pip releases (0.10.10+) removed it outright, so this uses the
replacement Tasks API instead.
"""

import enum
import logging
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from gestures import GestureRecognizer

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CAM_INDEX = 0
MAX_HANDS = 1
DETECTION_CONF = 0.6
TRACKING_CONF = 0.6

MODEL_PATH = Path(__file__).with_name("hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)

# Standard 21-point hand skeleton connections (replaces the old
# mp.solutions.hands.HAND_CONNECTIONS, which no longer exists)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),          # index
    (5, 9), (9, 10), (10, 11), (11, 12),     # middle
    (9, 13), (13, 14), (14, 15), (15, 16),   # ring
    (13, 17), (17, 18), (18, 19), (19, 20),  # pinky
    (0, 17),                                 # palm base
]


class CameraError(RuntimeError):
    """The camera couldn't be opened or stopped delivering frames."""


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def ensure_model():
    """Download the hand landmarker model bundle if it's not present."""
    if MODEL_PATH.exists():
        return
    log.info("Downloading model to %s ...", MODEL_PATH)
    # Download to a temporary name so an interrupted download isn't
    # mistaken for a valid model on the next run.
    partial = MODEL_PATH.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, partial)
    partial.replace(MODEL_PATH)
    log.info("Done.")


def create_detector():
    """Build the MediaPipe HandLandmarker in VIDEO mode."""
    base_options = mp_python.BaseOptions(model_asset_path=str(MODEL_PATH))
    options = mp_vision.HandLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.VIDEO,
        num_hands=MAX_HANDS,
        min_hand_detection_confidence=DETECTION_CONF,
        min_tracking_confidence=TRACKING_CONF,
    )
    return mp_vision.HandLandmarker.create_from_options(options)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
class Camera:
    """A webcam that returns BGR frames, mirrored for natural interaction."""

    def __init__(self, index: int = CAM_INDEX, mirror: bool = True):
        self.index = index
        self.mirror = mirror
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            self._cap.release()
            raise CameraError(f"Could not open camera index {index}")

    def read(self) -> np.ndarray:
        ok, frame = self._cap.read()
        if not ok:
            raise CameraError(f"Failed to read frame from camera {self.index}")
        return cv2.flip(frame, 1) if self.mirror else frame

    def close(self):
        self._cap.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class Detection:
    """What the pipeline found in one frame. Coordinates are in pixels."""

    timestamp: float                         # time.monotonic() at processing
    frame_size: tuple[int, int]              # (width, height)
    proc_ms: float                           # detection + recognition time
    landmarks: np.ndarray | None = None      # (21, 2) raw; None if no hand
    extended: dict[str, bool] | None = None  # per-finger extended flags
    posture: str | None = None               # debounced posture label
    event: str | None = None                 # gesture fired on this frame
    raw_centroid: np.ndarray | None = None   # palm centroid, unfiltered
    centroid: np.ndarray | None = None       # palm centroid, smoothed

    @property
    def hand_found(self) -> bool:
        return self.landmarks is not None

    @property
    def target_norm(self) -> tuple[float, float] | None:
        """Smoothed palm centroid normalized to 0-1 (for panel mapping)."""
        if self.centroid is None:
            return None
        w, h = self.frame_size
        return float(self.centroid[0] / w), float(self.centroid[1] / h)


class DetectionPipeline:
    """Hand landmarks + gesture recognition, one frame at a time.

    Frames must come from a single continuous stream: the recognizer keeps
    history between calls. Call reset() after a gap in the stream. Not
    thread-safe; create and use it on one thread.
    """

    def __init__(self, smoothing: bool = True):
        ensure_model()
        self._detector = create_detector()
        self._recognizer = GestureRecognizer(smoothing=smoothing)
        self._clock_start = time.monotonic()
        self._last_ts_ms = -1

    @property
    def smoothing(self) -> bool:
        return self._recognizer.smoothing

    def process(self, frame: np.ndarray) -> Detection:
        """Detect and classify the hand in a BGR frame."""
        start = time.perf_counter()
        now = time.monotonic()
        h, w = frame.shape[:2]

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect_for_video(image, self._next_timestamp_ms(now))

        if not result.hand_landmarks:
            self._recognizer.reset()  # also clears the smoothing filters
            return Detection(now, (w, h), _ms_since(start))

        landmarks = result.hand_landmarks[0]  # first detected hand
        posture, event = self._recognizer.update(landmarks, now, w, h)
        return Detection(
            timestamp=now,
            frame_size=(w, h),
            proc_ms=_ms_since(start),
            landmarks=np.array([(lm.x * w, lm.y * h) for lm in landmarks]),
            extended=self._recognizer.extended,
            posture=posture,
            event=event,
            raw_centroid=self._recognizer.raw_centroid,
            centroid=self._recognizer.centroid,
        )

    def reset(self):
        """Forget gesture history and smoothing state."""
        self._recognizer.reset()

    def close(self):
        self._detector.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def _next_timestamp_ms(self, now: float) -> int:
        # MediaPipe needs strictly increasing timestamps, and two frames can
        # land on the same millisecond.
        ts = max(int((now - self._clock_start) * 1000), self._last_ts_ms + 1)
        self._last_ts_ms = ts
        return ts


def _ms_since(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def draw_detection(frame: np.ndarray, detection: Detection):
    """Draw the hand skeleton and the raw (red) vs smoothed (green) centroid."""
    if not detection.hand_found:
        return
    pts = detection.landmarks.astype(int)
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, tuple(pts[a]), tuple(pts[b]), (255, 255, 255), 2)
    for x, y in pts:
        cv2.circle(frame, (x, y), 3, (255, 180, 0), -1)

    rx, ry = detection.raw_centroid.astype(int)
    sx, sy = detection.centroid.astype(int)
    cv2.circle(frame, (rx, ry), 4, (0, 0, 255), -1)
    cv2.circle(frame, (sx, sy), 10, (0, 255, 0), -1)


class FpsMeter:
    """Frames per second, smoothed with an exponential moving average."""

    def __init__(self, alpha: float = 0.1):
        self.alpha = alpha
        self.fps = 0.0
        self._last = time.monotonic()

    def tick(self) -> float:
        """Register a frame and return the current estimate."""
        now = time.monotonic()
        instant = 1.0 / max(now - self._last, 1e-6)
        self.fps = (1 - self.alpha) * self.fps + self.alpha * instant
        self._last = now
        return self.fps


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------
class WorkerState(enum.Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass(frozen=True, eq=False)
class FrameResult:
    """What the worker delivers for every captured frame."""

    frame: np.ndarray            # mirrored BGR frame, annotated if detecting
    detection: Detection | None  # None while paused
    fps: float


class DetectionWorker:
    """Runs capture + detection on a background thread.

    Controls, safe to call from any thread:
      start()   open the camera and begin detecting
      pause()   keep streaming video but skip detection
      resume()  detect again; gesture history starts fresh
      stop()    end the thread and release the camera

    A control that doesn't apply to the current state (e.g. pause() while
    stopped) is ignored, so a GUI can wire buttons straight to them.

    All callbacks run on the worker thread, and state changes are reported
    in order. A GUI must not touch its widgets from them; it should hand the
    data over to its own thread (e.g. by emitting a Qt signal).
    """

    def __init__(
        self,
        on_frame: Callable[[FrameResult], None],
        *,
        camera_index: int = CAM_INDEX,
        smoothing: bool = True,
        annotate: bool = True,
        on_state_change: Callable[[WorkerState], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ):
        self.camera_index = camera_index
        self.smoothing = smoothing
        self.annotate = annotate
        self._on_frame = on_frame
        self._on_state_change = on_state_change
        self._on_error = on_error

        # Reentrant so a state callback can read `state` while the worker
        # holds the lock to report STOPPED.
        self._lock = threading.RLock()
        self._state = WorkerState.STOPPED
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._reported_state: WorkerState | None = None  # worker thread only

    @property
    def state(self) -> WorkerState:
        with self._lock:
            return self._state

    def start(self):
        with self._lock:
            if self._state is not WorkerState.STOPPED:
                return
            self._state = WorkerState.RUNNING
            self._stop_requested.clear()
            self._thread = threading.Thread(
                target=self._run, name="detection-worker", daemon=True
            )
            self._thread.start()

    def pause(self):
        self._transition(WorkerState.RUNNING, WorkerState.PAUSED)

    def resume(self):
        self._transition(WorkerState.PAUSED, WorkerState.RUNNING)

    def stop(self, timeout: float | None = 2.0):
        """Ask the thread to finish and wait up to `timeout` seconds for it."""
        with self._lock:
            thread = self._thread
        if thread is None:
            return
        self._stop_requested.set()
        if thread is not threading.current_thread():  # e.g. stop() in a callback
            thread.join(timeout)

    def _transition(self, current: WorkerState, new: WorkerState):
        with self._lock:
            if self._state is current:
                self._state = new

    def _report(self, state: WorkerState):
        if state is self._reported_state:
            return
        self._reported_state = state
        if self._on_state_change:
            self._on_state_change(state)

    def _run(self):
        self._reported_state = None
        try:
            self._report(self.state)
            with (
                Camera(self.camera_index) as camera,
                DetectionPipeline(smoothing=self.smoothing) as pipeline,
            ):
                self._loop(camera, pipeline)
        except Exception as exc:
            log.exception("Detection worker stopped on error")
            if self._on_error:
                self._on_error(exc)
        finally:
            # Report under the lock so that a start() right after can't
            # deliver its RUNNING before this STOPPED.
            with self._lock:
                self._state = WorkerState.STOPPED
                self._report(WorkerState.STOPPED)

    def _loop(self, camera: Camera, pipeline: DetectionPipeline):
        fps = FpsMeter()
        was_paused = False
        while not self._stop_requested.is_set():
            frame = camera.read()
            state = self.state
            self._report(state)

            detection = None
            paused = state is WorkerState.PAUSED
            if not paused:
                if was_paused:
                    # Smoothing and gesture history from before the pause
                    # would blend into the new frames (e.g. a fake swipe).
                    pipeline.reset()
                detection = pipeline.process(frame)
                if self.annotate:
                    draw_detection(frame, detection)
            was_paused = paused

            self._on_frame(FrameResult(frame, detection, fps.tick()))
