"""Main window: live video, detection results, controls and event log.

Every event (gestures, commands, errors and recoveries) goes to the "Eventos"
list and, with the program's state at that moment, to the session's CSV log
(events.EventLog).
"""

from datetime import datetime

from PySide6.QtCore import Qt, Signal, Slot
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

from comms.maestro import LinkInfo
from comms.protocol import Mode, Transport
from detection import FrameResult, WorkerState
from events import Event, EventLog, Level
from gui.bridge import DetectionBridge
from gui.maestro import MaestroBridge
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
    WorkerState.RECONNECTING: ("Reconectando cámara…", "#c0392b"),
}

LEVEL_MARKS = {Level.INFO: "·", Level.WARNING: "⚠", Level.ERROR: "✖"}
# Shown in the log file only: the gesture line already says it
QUIET_EVENTS = {"COMANDO_ACEPTADO", "GESTO_DETECTADO"}

MODE_LABELS = {
    Mode.OFF: "apagado",
    Mode.HOLD: "quieto",
    Mode.TARGET: "siguiendo la mano",
    Mode.GESTURE: "gesto",
    Mode.MANUAL: "ángulos manuales",
}

MAX_CAMERA_INDEX = 10
MAX_LOG_ENTRIES = 200
EMPTY = "—"


def controller_text(info: LinkInfo) -> str:
    status = info.status
    if status is None:
        return EMPTY
    if status.is_controller:
        return "este programa"
    if status.controller is Transport.UDP:
        return "otro programa (UDP)"
    if status.controller is Transport.WEBSOCKET:
        return "un navegador"
    return "nadie"


class MainWindow(QMainWindow):
    _event_from_thread = Signal(object)   # see record_from_any_thread

    def __init__(self, bridge: DetectionBridge, maestro: MaestroBridge | None = None,
                 event_log: EventLog | None = None):
        super().__init__()
        self.setWindowTitle("SOFIA — Control")
        self._bridge = bridge
        self._maestro = maestro or MaestroBridge(parent=self)
        self._event_log = event_log or EventLog(None)
        self._link_info: LinkInfo | None = None
        # Set between Finalizar and the worker reporting STOPPED
        self._stopping = False

        self._build_ui()
        self._connect_signals()
        self._refresh_state()

        log_path = self._event_log.path
        self.record(Event(Level.INFO, "gui", "SESION_INICIADA",
                          f"registro en {log_path}" if log_path else "sin archivo de registro"))

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
        panel.addWidget(self._build_maestro_group())
        panel.addStretch()
        return panel

    def _build_maestro_group(self) -> QGroupBox:
        self.maestro_check = QCheckBox("Enviar gestos y mano al Maestro")
        self.maestro_check.setToolTip(
            f"Conecta con el Maestro en {self._maestro.host}:{self._maestro.port} (UDP). "
            "La PC debe estar en la red Wi‑Fi SOFIA-Maestro."
        )
        self.link_label = QLabel(EMPTY)
        self.control_label = QLabel(EMPTY)
        self.mode_label = QLabel(EMPTY)
        self.latency_label = QLabel(EMPTY)
        self.arm_button = QPushButton("Armar")
        self.arm_button.setToolTip("Toma el control y enciende los servos.")
        self.maestro_stop_button = QPushButton("STOP")
        self.maestro_stop_button.setToolTip(
            "Detiene el panal y libera el control, lo tenga quien lo tenga."
        )
        self.maestro_stop_button.setStyleSheet("font-weight: bold; color: #c0392b;")
        for button in (self.arm_button, self.maestro_stop_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        buttons = QHBoxLayout()
        buttons.addWidget(self.arm_button)
        buttons.addWidget(self.maestro_stop_button)

        form = QFormLayout()
        form.addRow(self.maestro_check)
        form.addRow("Enlace:", self.link_label)
        form.addRow("Control:", self.control_label)
        form.addRow("Modo:", self.mode_label)
        form.addRow("Latencia:", self.latency_label)
        form.addRow(buttons)
        group = QGroupBox("Maestro (ESP32)")
        group.setLayout(form)
        self._refresh_maestro(None)
        return group

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
        if self._event_log.path:
            group.setToolTip(f"También se guardan en {self._event_log.path}")
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
        self._bridge.event.connect(self.record)

        self.maestro_check.toggled.connect(self._toggle_maestro)
        self.arm_button.clicked.connect(self._maestro.arm)
        self.maestro_stop_button.clicked.connect(self._maestro.stop)
        self._maestro.info_changed.connect(self._refresh_maestro)
        self._maestro.event.connect(self.record)
        self._event_from_thread.connect(self.record)

    # Controls --------------------------------------------------------------
    @Slot()
    def _start(self):
        self._bridge.camera_index = self.camera_spin.value()
        self._bridge.smoothing = self.smoothing_check.isChecked()
        self._bridge.start()
        self._refresh_state()
        self.record(Event(Level.INFO, "gui", "DETECCION_INICIADA",
                          f"cámara {self._bridge.camera_index}, suavizado "
                          f"{'sí' if self._bridge.smoothing else 'no'}"))

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
        self.record(Event(Level.INFO, "gui", "DETECCION_FINALIZADA", "por el usuario"))

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
        reconnecting = state is WorkerState.RECONNECTING and not self._stopping
        if reconnecting:
            self.video.placeholder = "Cámara perdida. Reintentando…"
            self.video.clear()
            self._clear_detection()
        else:
            self.video.placeholder = "Cámara detenida. Pulsa «Iniciar»."
        if not running:
            self._maestro.clear_target()   # no detection, no hand to follow
        self.start_button.setEnabled(state is WorkerState.STOPPED)
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(paused)
        self.stop_button.setEnabled(running or paused or reconnecting)
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
            self._maestro.clear_target()
            self.posture_label.setText(EMPTY)
            self.target_label.setText("detección en pausa")
            self.perf_label.setText(f"{result.fps:.0f} FPS")
            return

        self.posture_label.setText(detection.posture or EMPTY)
        target = detection.target_norm
        # The Maestro holds the pose 0.5 s after the hand is lost.
        if target:
            self._maestro.set_target(*target)
        else:
            self._maestro.clear_target()
        self.target_label.setText(
            f"({target[0]:.2f}, {target[1]:.2f})" if target else "sin mano"
        )
        self.perf_label.setText(f"{result.fps:.0f} FPS · {detection.proc_ms:.0f} ms")

    @Slot(str, float)
    def _log_gesture(self, event: str, timestamp: float):
        self._maestro.gesture(event)
        action = ACTION_LABELS.get(event, EMPTY)
        self.gesture_label.setText(event)
        self.action_label.setText(action)
        self._add_log_line(f"{event} → {action}")
        sent = "se envía al Maestro" if self._maestro.is_open else "Maestro no conectado"
        self.record(Event(Level.INFO, "camara", "GESTO_DETECTADO", event, sent))

    def _add_log_line(self, text: str):
        clock = datetime.now().strftime("%H:%M:%S")
        self.log_list.insertItem(0, f"{clock}  {text}")
        while self.log_list.count() > MAX_LOG_ENTRIES:
            self.log_list.takeItem(self.log_list.count() - 1)

    @Slot(object)
    def record(self, event: Event):
        """Log an event to the file (with the current state) and the window."""
        self._event_log.record(event, self._context())
        if event.name in QUIET_EVENTS:
            return
        self._add_log_line(f"{LEVEL_MARKS[event.level]} {event}")
        if event.level is not Level.INFO:
            self.statusBar().showMessage(f"{event.name}: {event.detail}", 8_000)

    def record_from_any_thread(self, event: Event):
        """record(), queued to the UI thread when called from another one."""
        self._event_from_thread.emit(event)

    def _context(self) -> str:
        """The conditions an event happened in, for the log file."""
        parts = [
            f"deteccion={self._bridge.state.value}",
            f"camara={self._bridge.camera_index}",
            f"suavizado={'si' if self._bridge.smoothing else 'no'}",
        ]
        info = self._link_info
        if not self._maestro.is_open:
            parts.append("maestro=desconectado")
        elif info is None:
            parts.append("maestro=conectando")
        else:
            parts.append(f"maestro={'en_linea' if info.online else 'sin_respuesta'}")
            if info.status:
                parts.append(f"control={controller_text(info)}")
                parts.append(f"modo={info.status.mode.name}")
            if info.p95_ms is not None and info.p95_ms != float("inf"):
                parts.append(f"p95={info.p95_ms:.0f}ms")
        return "; ".join(parts)

    @Slot(str)
    def _show_error(self, message: str):
        # The worker already reported it as an event
        QMessageBox.warning(self, "Error de detección", message)

    def _clear_detection(self):
        for label in (self.posture_label, self.target_label, self.perf_label):
            label.setText(EMPTY)

    # Maestro ---------------------------------------------------------------
    @Slot(bool)
    def _toggle_maestro(self, enabled: bool):
        if enabled:
            self._maestro.open()
            self.link_label.setText("conectando…")
            self.record(Event(Level.INFO, "gui", "MAESTRO_CONECTADO",
                              f"{self._maestro.host}:{self._maestro.port}"))
        else:
            self._maestro.close()
            self._refresh_maestro(None)
            self.record(Event(Level.INFO, "gui", "MAESTRO_DESCONECTADO", "por el usuario"))

    @Slot(object)
    def _refresh_maestro(self, info: LinkInfo | None):
        is_open = self._maestro.is_open
        self._link_info = info if is_open else None
        self.arm_button.setEnabled(is_open)
        self.maestro_stop_button.setEnabled(is_open)
        if info is None or not is_open:
            self.link_label.setText("desconectado" if not is_open else "conectando…")
            for label in (self.control_label, self.mode_label, self.latency_label):
                label.setText(EMPTY)
            return

        status = info.status
        if not info.online or status is None:
            self.link_label.setText('<span style="color:#c0392b">sin respuesta</span>')
        elif status.simulated:
            self.link_label.setText("en línea · servos simulados")
        elif not status.healthy:
            self.link_label.setText('en línea · <span style="color:#d08a00">placas PWM sin respuesta</span>')
        else:
            self.link_label.setText(f"en línea · {status.powered}/32 servos")
        self.control_label.setText(controller_text(info))
        if status:
            mode = MODE_LABELS[status.mode]
            self.mode_label.setText(f"{mode} ({status.gesture.name})" if status.gesture else mode)
        if info.p95_ms is not None and info.within_500 is not None:
            p95 = f"{info.p95_ms:.0f} ms" if info.p95_ms != float("inf") else "> 800 ms"
            self.latency_label.setText(f"p95 {p95} · {info.within_500:.1%} < 500 ms")

    # Qt events -------------------------------------------------------------
    def closeEvent(self, event):
        self._bridge.shutdown()  # releases the camera before exiting
        self._maestro.close()    # sends STOP if this program controls SOFIA
        self.record(Event(Level.INFO, "gui", "SESION_FINALIZADA"))
        self._event_log.close()
        super().closeEvent(event)
