"""Shared fixtures: a fake camera and a fake pipeline, so tests need no webcam."""

import os

import pytest

# Run Qt without a display (must be set before Qt is imported)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import detection  # noqa: E402
from fakes import FakeCamera, FakePipeline  # noqa: E402


@pytest.fixture
def fake_camera(monkeypatch):
    monkeypatch.setattr(detection, "Camera", FakeCamera)


@pytest.fixture
def fake_pipeline(monkeypatch):
    FakePipeline.instances.clear()
    monkeypatch.setattr(detection, "DetectionPipeline", FakePipeline)
    return FakePipeline.instances
