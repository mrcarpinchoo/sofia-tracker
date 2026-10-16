"""MainWindow + DetectionBridge, driven through the real buttons."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from detection import WorkerState
from fakes import FAILING_CAMERA
from gestures import SOFIA_ACTIONS
from gui.bridge import DetectionBridge
from gui.main_window import ACTION_LABELS, MainWindow


@pytest.fixture
def window(qtbot, fake_camera, fake_pipeline):
    bridge = DetectionBridge()
    win = MainWindow(bridge)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    bridge.shutdown()


def enabled_buttons(win) -> set[str]:
    buttons = {
        "start": win.start_button, "pause": win.pause_button,
        "resume": win.resume_button, "stop": win.stop_button,
    }
    return {name for name, button in buttons.items() if button.isEnabled()}


def click(qtbot, button):
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def wait_for_state(qtbot, win, state):
    qtbot.waitUntil(lambda: win._bridge.state is state)


def test_action_labels_cover_every_gesture():
    assert set(ACTION_LABELS) == set(SOFIA_ACTIONS)


def test_initially_only_start_is_enabled(window):
    assert enabled_buttons(window) == {"start"}
    assert window.settings_group.isEnabled()
    assert not window.video.has_frame


def test_start_shows_video_and_detection(qtbot, window):
    click(qtbot, window.start_button)

    qtbot.waitUntil(lambda: window.video.has_frame)
    qtbot.waitUntil(lambda: window.posture_label.text() == "V")
    assert enabled_buttons(window) == {"pause", "stop"}
    assert not window.settings_group.isEnabled()


def test_settings_apply_on_start(qtbot, window, fake_pipeline):
    window.camera_spin.setValue(3)
    window.smoothing_check.setChecked(False)
    click(qtbot, window.start_button)

    qtbot.waitUntil(lambda: bool(fake_pipeline))
    assert window._bridge.camera_index == 3
    assert fake_pipeline[0].smoothing is False


def test_pause_and_resume(qtbot, window):
    click(qtbot, window.start_button)
    qtbot.waitUntil(lambda: window.video.has_frame)

    click(qtbot, window.pause_button)
    assert window._bridge.state is WorkerState.PAUSED
    assert enabled_buttons(window) == {"resume", "stop"}
    qtbot.waitUntil(lambda: window.target_label.text() == "detección en pausa")

    click(qtbot, window.resume_button)
    assert window._bridge.state is WorkerState.RUNNING
    assert enabled_buttons(window) == {"pause", "stop"}
    qtbot.waitUntil(lambda: window.posture_label.text() == "V")


def test_space_toggles_pause(qtbot, window):
    click(qtbot, window.start_button)
    qtbot.waitUntil(lambda: window.video.has_frame)

    qtbot.keyClick(window, Qt.Key.Key_Space)
    assert window._bridge.state is WorkerState.PAUSED
    qtbot.keyClick(window, Qt.Key.Key_Space)
    assert window._bridge.state is WorkerState.RUNNING


def test_gestures_are_logged(qtbot, window):
    click(qtbot, window.start_button)

    qtbot.waitUntil(lambda: window.log_list.count() > 0)
    assert window.gesture_label.text() == "V"
    assert window.action_label.text() == ACTION_LABELS["V"]
    assert f"V → {ACTION_LABELS['V']}" in window.log_list.item(0).text()


def test_stop_releases_and_resets(qtbot, window):
    click(qtbot, window.start_button)
    qtbot.waitUntil(lambda: window.video.has_frame)

    click(qtbot, window.stop_button)
    qtbot.waitUntil(lambda: enabled_buttons(window) == {"start"})
    assert not window.video.has_frame
    assert window.settings_group.isEnabled()


def test_camera_error_is_shown_and_returns_to_stopped(qtbot, window, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text: warnings.append(text))

    window.camera_spin.setMaximum(FAILING_CAMERA)
    window.camera_spin.setValue(FAILING_CAMERA)
    click(qtbot, window.start_button)

    qtbot.waitUntil(lambda: bool(warnings))
    assert "99" in warnings[0]
    qtbot.waitUntil(lambda: enabled_buttons(window) == {"start"})
