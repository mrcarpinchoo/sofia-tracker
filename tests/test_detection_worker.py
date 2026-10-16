"""DetectionWorker: thread lifecycle and start/pause/resume/stop controls."""

import threading

import pytest

from detection import DetectionWorker, WorkerState
from fakes import FAILING_CAMERA, FRAME_SHAPE, wait_until


class Recorder:
    """Collects everything a worker reports through its callbacks."""

    def __init__(self):
        self.frames = []
        self.states = []
        self.errors = []

    def worker(self, **kwargs) -> DetectionWorker:
        return DetectionWorker(
            self.frames.append,
            on_state_change=self.states.append,
            on_error=self.errors.append,
            **kwargs,
        )

    def wait_for_frames(self, count: int = 3):
        start = len(self.frames)
        wait_until(lambda: len(self.frames) >= start + count)
        return self.frames[-1]


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def worker(recorder, fake_camera, fake_pipeline):
    w = recorder.worker()
    yield w
    w.stop()


def test_controls_that_dont_apply_are_ignored(worker):
    worker.pause()
    worker.resume()
    worker.stop()
    assert worker.state is WorkerState.STOPPED

    worker.start()
    worker.resume()  # not paused
    assert worker.state is WorkerState.RUNNING


def test_running_delivers_detections(worker, recorder):
    worker.start()
    result = recorder.wait_for_frames()
    assert result.frame.shape == FRAME_SHAPE
    assert result.detection is not None
    assert result.detection.posture == "V"


def test_pause_streams_video_without_detection(worker, recorder):
    worker.start()
    recorder.wait_for_frames()
    worker.pause()
    assert worker.state is WorkerState.PAUSED

    recorder.wait_for_frames()
    result = recorder.wait_for_frames(1)
    assert result.detection is None
    assert result.frame.shape == FRAME_SHAPE


def test_resume_detects_again_with_fresh_history(worker, recorder, fake_pipeline):
    worker.start()
    recorder.wait_for_frames()
    worker.pause()
    recorder.wait_for_frames()
    worker.resume()

    recorder.wait_for_frames()
    assert recorder.wait_for_frames(1).detection is not None
    assert fake_pipeline[0].resets == 1


def test_stop_releases_and_reports_states_in_order(worker, recorder):
    worker.start()
    recorder.wait_for_frames()
    worker.pause()
    recorder.wait_for_frames()
    worker.resume()
    recorder.wait_for_frames()
    worker.stop()

    assert worker.state is WorkerState.STOPPED
    assert recorder.states == [
        WorkerState.RUNNING, WorkerState.PAUSED,
        WorkerState.RUNNING, WorkerState.STOPPED,
    ]
    assert not recorder.errors


def test_can_restart_after_stop(worker, recorder, fake_pipeline):
    worker.start()
    recorder.wait_for_frames()
    worker.stop()
    worker.start()
    recorder.wait_for_frames()
    worker.stop()

    assert recorder.states[-2:] == [WorkerState.RUNNING, WorkerState.STOPPED]
    assert len(fake_pipeline) == 2  # a fresh pipeline per run


def test_camera_error_is_reported_and_stops(recorder, fake_camera, fake_pipeline):
    worker = recorder.worker(camera_index=FAILING_CAMERA)
    worker.start()
    worker.stop()

    assert worker.state is WorkerState.STOPPED
    assert len(recorder.errors) == 1
    assert recorder.states == [WorkerState.RUNNING, WorkerState.STOPPED]
    assert not recorder.frames


def test_stop_from_a_callback_does_not_deadlock(fake_camera, fake_pipeline):
    worker = DetectionWorker(lambda result: worker.stop())
    worker.start()
    wait_until(lambda: worker.state is WorkerState.STOPPED)
    wait_until(lambda: not any(
        t.name == "detection-worker" for t in threading.enumerate()
    ))


def test_real_pipeline_on_blank_frames(recorder, fake_camera):
    """The real MediaPipe pipeline runs on the worker thread (no hand here)."""
    worker = recorder.worker()
    worker.start()
    try:
        result = recorder.wait_for_frames()
    finally:
        worker.stop()

    assert not recorder.errors
    assert result.detection is not None
    assert not result.detection.hand_found
    assert result.detection.proc_ms > 0
