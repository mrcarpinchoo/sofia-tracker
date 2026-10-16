"""Main window: live video, detection results, controls and event log."""

from datetime import datetime

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from detection import FrameResult, WorkerState
from gui.bridge import DetectionBridge
from gui.video_view import VideoView

# Spanish UI text for each SOFIA gesture (keys match gestures.SOFIA_ACTIONS)
ACTION_LABELS = {
    "INDEX": "mirar arriba, luego abajo",
    "V": "dibujar un 8 horizontal",
    "PALM": "mirar a la derecha, luego a la izquierda",
    "SWIPE_LEFT": "ola hacia la izquierda",
    "SWIPE_RIGHT": "ola hacia la derecha",
    "GRAB": "sobresalto / volver al reposo",
}

STATE_LABELS = {
    WorkerState.STOPPED: ("Detenido", "#888888"),
    WorkerState.RUNNING: ("Detectando", "#2e9d4f"),
    WorkerState.PAUSED: ("En pausa", "#d08a00"),
}

MAX_CAMERA_INDEX = 10
MAX_LOG_ENTRIES = 200
EMPTY = "—"


class MainWindow(QMainWindow):
    def __init__(self, bridge: DetectionBridge):
        super().__init__()
        self.setWindowTitle("SOFIA — Control")
        self._bridge = bridge
        # Set between Finalizar and the worker reporting STOPPED
        self._stopping = False

        self._build_ui()
        self._connect_signals()
        self._refresh_state()

    # Layout ----------------------------------------------------------------
    def _build_ui(self):
        self.video = VideoView(placeholder="Cámara detenida. Pulsa «Iniciar».")

        top = QHBoxLayout()
        top.addWidget(self.video, stretch=1)
        top.addLayout(self._build_side_panel())

        root = QVBoxLayout()
        root.addLayout(top, stretch=1)
        root.addLayout(self._build_controls())
        root.addWidget(self._build_log())

        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        self.state_badge = QLabel()
        self.statusBar().addPermanentWidget(self.state_badge)

    def _build_side_panel(self) -> QVBoxLayout:
        self.posture_label = QLabel(EMPTY)
        self.gesture_label = QLabel(EMPTY)
        self.action_label = QLabel(EMPTY)
        self.action_label.setWordWrap(True)
        self.target_label = QLabel(EMPTY)
        self.perf_label = QLabel(EMPTY)

        detection_form = QFormLayout()
        detection_form.addRow("Postura:", self.posture_label)
        detection_form.addRow("Último gesto:", self.gesture_label)
        detection_form.addRow("Acción SOFIA:", self.action_label)
        detection_form.addRow("Objetivo:", self.target_label)
        detection_form.addRow("Rendimiento:", self.perf_label)
        detection_group = QGroupBox("Detección")
        detection_group.setLayout(detection_form)

        self.camera_spin = QSpinBox()
        self.camera_spin.setRange(0, MAX_CAMERA_INDEX)
        self.camera_spin.setValue(self._bridge.camera_index)
        self.smoothing_check = QCheckBox("Suavizado")
        self.smoothing_check.setChecked(self._bridge.smoothing)

        settings_form = QFormLayout()
        settings_form.addRow("Cámara:", self.camera_spin)
        settings_form.addRow(self.smoothing_check)
        self.settings_group = QGroupBox("Configuración")
        self.settings_group.setToolTip("Se aplica al pulsar «Iniciar».")
        self.settings_group.setLayout(settings_form)

        panel = QVBoxLayout()
        panel.addWidget(detection_group)
        panel.addWidget(self.settings_group)
        panel.addStretch()
        return panel

    def _build_controls(self) -> QHBoxLayout:
        icon = self.style().standardIcon
        self.start_button = QPushButton(icon(QStyle.StandardPixmap.SP_MediaPlay), "Iniciar")
        self.pause_button = QPushButton(icon(QStyle.StandardPixmap.SP_MediaPause), "Detener")
        self.resume_button = QPushButton(
            icon(QStyle.StandardPixmap.SP_MediaSeekForward), "Reanudar"
        )
        self.stop_button = QPushButton(icon(QStyle.StandardPixmap.SP_MediaStop), "Finalizar")

        self.start_button.setToolTip("Abre la cámara y comienza la detección.")
        self.pause_button.setToolTip("Pausa la detección; el video sigue en vivo. (Espacio)")
        self.resume_button.setToolTip("Reanuda la detección. (Espacio)")
        self.stop_button.setToolTip("Termina la detección y libera la cámara.")

        controls = QHBoxLayout()
        for button in (self.start_button, self.pause_button,
                       self.resume_button, self.stop_button):
            # Keep Space for the pause/resume shortcut instead of "click".
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            controls.addWidget(button)
        controls.addStretch()
        return controls

    def _build_log(self) -> QGroupBox:
        self.log_list = QListWidget()
        self.log_list.setMaximumHeight(140)
        layout = QVBoxLayout()
        layout.addWidget(self.log_list)
        group = QGroupBox("Eventos")
        group.setLayout(layout)
        return group

    def _connect_signals(self):
        self.start_button.clicked.connect(self._start)
        self.pause_button.clicked.connect(self._pause)
        self.resume_button.clicked.connect(self._resume)
        self.stop_button.clicked.connect(self._stop)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._toggle_pause)

        self._bridge.frame_ready.connect(self._show_frame)
        self._bridge.state_changed.connect(self._on_state_changed)
        self._bridge.gesture_detected.connect(self._log_gesture)
        self._bridge.error.connect(self._show_error)

    # Controls --------------------------------------------------------------
    @Slot()
    def _start(self):
        self._bridge.camera_index = self.camera_spin.value()
        self._bridge.smoothing = self.smoothing_check.isChecked()
        self._bridge.start()
        self._refresh_state()

    @Slot()
    def _pause(self):
        self._bridge.pause()
        self._refresh_state()

    @Slot()
    def _resume(self):
        self._bridge.resume()
        self._refresh_state()

    @Slot()
    def _toggle_pause(self):
        if self._bridge.state is WorkerState.RUNNING:
            self._pause()
        elif self._bridge.state is WorkerState.PAUSED:
            self._resume()

    @Slot()
    def _stop(self):
        self._stopping = True
        self._bridge.stop()
        self._refresh_state()

    # Bridge signals --------------------------------------------------------
    @Slot(object)
    def _on_state_changed(self, _state: WorkerState):
        # The signal can arrive late; the bridge always has the current state.
        self._refresh_state()

    def _refresh_state(self):
        """Sync controls with the worker's actual state."""
        state = self._bridge.state
        if state is WorkerState.STOPPED:
            self._stopping = False
            self.video.clear()
            self._clear_detection()

        running = state is WorkerState.RUNNING and not self._stopping
        paused = state is WorkerState.PAUSED and not self._stopping
        self.start_button.setEnabled(state is WorkerState.STOPPED)
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(paused)
        self.stop_button.setEnabled(running or paused)
        self.settings_group.setEnabled(state is WorkerState.STOPPED)

        text, color = STATE_LABELS[state]
        if self._stopping:
            text = "Finalizando…"
        self.state_badge.setText(f'<span style="color:{color}">●</span> {text}')

    @Slot(object)
    def _show_frame(self, result: FrameResult):
        self.video.set_frame(result.frame)

        detection = result.detection
        if detection is None:  # paused: video only
            self.posture_label.setText(EMPTY)
            self.target_label.setText("detección en pausa")
            self.perf_label.setText(f"{result.fps:.0f} FPS")
            return

        self.posture_label.setText(detection.posture or EMPTY)
        target = detection.target_norm
        self.target_label.setText(
            f"({target[0]:.2f}, {target[1]:.2f})" if target else "sin mano"
        )
        self.perf_label.setText(f"{result.fps:.0f} FPS · {detection.proc_ms:.0f} ms")

    @Slot(str, float)
    def _log_gesture(self, event: str, timestamp: float):
        action = ACTION_LABELS.get(event, EMPTY)
        self.gesture_label.setText(event)
        self.action_label.setText(action)

        clock = datetime.now().strftime("%H:%M:%S")
        self.log_list.insertItem(0, f"{clock}  {event} → {action}")
        while self.log_list.count() > MAX_LOG_ENTRIES:
            self.log_list.takeItem(self.log_list.count() - 1)

    @Slot(str)
    def _show_error(self, message: str):
        self.statusBar().showMessage(f"Error: {message}", 10_000)
        QMessageBox.warning(self, "Error de detección", message)

    def _clear_detection(self):
        for label in (self.posture_label, self.target_label, self.perf_label):
            label.setText(EMPTY)

    # Qt events -------------------------------------------------------------
    def closeEvent(self, event):
        self._bridge.shutdown()  # releases the camera before exiting
        super().closeEvent(event)
