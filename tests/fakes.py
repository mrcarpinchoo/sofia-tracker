"""Test doubles for the camera and the detection pipeline."""

import time

import numpy as np

from detection import CameraError, Detection

FRAME_SHAPE = (480, 640, 3)
FAILING_CAMERA = 99
GESTURE_EVERY = 10   # FakePipeline fires a "V" every this many frames


class FakeCamera:
    """Black frames at ~100 FPS; camera FAILING_CAMERA can't be opened."""

    def __init__(self, index: int = 0, mirror: bool = True):
        if index == FAILING_CAMERA:
            raise CameraError(f"Could not open camera index {index}")
        self.index = index

    def read(self) -> np.ndarray:
        time.sleep(0.01)
        return np.zeros(FRAME_SHAPE, np.uint8)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


class FakePipeline:
    """Sees a hand holding "V" on every frame and fires it periodically."""

    instances: list["FakePipeline"] = []

    def __init__(self, smoothing: bool = True):
        self.smoothing = smoothing
        self.frames = 0
        self.resets = 0
        FakePipeline.instances.append(self)

    def process(self, frame: np.ndarray) -> Detection:
        self.frames += 1
        h, w = frame.shape[:2]
        landmarks = np.full((21, 2), 100.0)
        return Detection(
            timestamp=time.monotonic(),
            frame_size=(w, h),
            proc_ms=1.0,
            landmarks=landmarks,
            extended={"index": True, "middle": True},
            posture="V",
            event="V" if self.frames % GESTURE_EVERY == 0 else None,
            raw_centroid=landmarks[0],
            centroid=landmarks[0],
        )

    def reset(self):
        self.resets += 1

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def wait_until(predicate, timeout: float = 5.0):
    """Poll `predicate` until it's true; fail after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met within timeout")
        time.sleep(0.01)
