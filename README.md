# SOFIA Tracker

Webcam hand tracking and gesture recognition for SOFIA. It uses MediaPipe's
HandLandmarker to track one hand, smooths the landmarks and a palm-centroid
point, and recognizes five gestures (three static, two dynamic), each mapped
to a SOFIA motion.

There are two front ends: a desktop GUI (PySide6) with start / pause /
resume / stop controls, and a command-line tracker with an OpenCV window.
The GUI can send the gestures and the hand's position to the SOFIA Maestro
(the ESP32 in `sofia-firmware`), which moves the panel; see
[ESP32 link](#esp32-link).

## Gestures

| Gesture | Type | SOFIA response |
|---|---|---|
| Index finger (only the index extended) | static | Look up, then down |
| V sign (index + middle extended) | static | Draw a horizontal 8 |
| Open palm (all five fingers extended) | static | Look right, then left |
| Swipe left / right | dynamic | Sweeping wave in the swipe direction |
| Grab (open palm → fist) | dynamic | Flinch / return to rest |

Static postures fire after you hold the hand **still** for about 0.5 s.
Holding a posture fires it only once. Because the open palm is a static
posture, opening your hand and pausing before a grab will fire it
first; close the hand within about 0.5 s to get only the grab.

Smoothing (an EMA on the 21 landmarks and on the palm centroid, plus a
5-frame majority vote) is on by default. Use `--no-smooth` to turn it off and
compare.

## Requirements

- Python **3.12** installed and available as `python3.12`. MediaPipe usually
  lags behind the newest Python releases.
- A webcam.
- Linux or macOS.

## Setup

### 1. Create the virtual environment with 3.12

From the `sofia-tracker` folder:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python --version        # should print Python 3.12.x
```

Don't copy `.venv` between machines or operating systems. Create it fresh
on each one.

### 2. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

The model file `hand_landmarker.task` is downloaded automatically on the
first run if it's missing.

## Usage

Activate the environment each time you open a new terminal:

```bash
source .venv/bin/activate
```

Find which camera index is your webcam:

```bash
python find_camera.py
```

Run the GUI:

```bash
python -m gui                     # default camera (index 0)
python -m gui --camera 1          # a specific camera (also selectable in the window)
python -m gui --no-smooth         # start with smoothing off
python -m gui --maestro 192.168.4.1:4210   # Maestro address (this is the default)
python -m gui --log-dir pruebas/    # where to write the event log (default logs/)
python -m gui --no-log              # don't write an event log
```

**Iniciar** opens the camera and starts detecting, **Detener** pauses
detection (the video keeps playing), **Reanudar** resumes it and
**Finalizar** stops and releases the camera. Space toggles pause/resume.
Detection runs on a background thread, so the window stays responsive.

### Errors, recovery and the event log

- **Camera lost while running** (unplugged, taken by another program): the
  state shows *Reconectando cámara…* and the camera is reopened every 2 s.
  Detection then resumes with a fresh gesture history. A camera that can't
  be opened when pressing **Iniciar** shows an error and detection stays
  stopped.
- **Maestro not answering**: after 3 commands without a response the link
  shows *sin respuesta*; the status poll keeps running and the link comes
  back by itself. If this GUI had pressed **Armar**, it sends ARM again at
  once (the Maestro releases control after 3 s of silence), unless the
  outage lasted more than 30 s, the user pressed STOP, or another client
  took control. Gestures detected while the link is down are dropped, not
  played late. A STOP without an answer is sent again until confirmed.
- **Unexpected exceptions** are logged and the application keeps running.

Every session writes `logs/eventos_<date>_<time>.csv` (time, level,
source, event, detail, action taken, and the program's state at that
moment): gestures detected, commands accepted or rejected, errors and
recoveries. Warnings and errors also appear in the **Eventos** list and
the status bar. `docs/MANEJO_DE_ERRORES.md` lists every event.

Or run the command-line tracker:

```bash
python tracker.py                 # default camera (index 0)
python tracker.py --camera 1      # a specific camera
python tracker.py --debug         # also show which fingers are detected as extended
python tracker.py --no-smooth     # disable smoothing (for comparison)
```

Press `q` to quit. Type `deactivate` to leave the virtual environment.

### Tests

The tests use a fake camera and pipeline, so they need neither a webcam nor
a display:

```bash
pip install -r requirements-dev.txt
pytest
```

### ESP32 link

The PC talks to the SOFIA Maestro (ESP32) over Wi‑Fi using the protocol in
`sofia-firmware/docs/PROTOCOL.md`. The tracker sends *what* to do (a
gesture, the hand's position); the Maestro turns it into the 32 joint
angles. Connect the PC to the Maestro's Wi‑Fi network (`SOFIA-Maestro`,
password `sofia-maestro`).

**From the GUI**, in the **Maestro (ESP32)** group:

1. Tick **Enviar gestos y mano al Maestro**. The group shows the link, who
   controls SOFIA, the Maestro's mode and the latency (95th percentile, and
   the share of commands answered within 500 ms).
2. Press **Armar** to take control and power the servos. Only one client
   (this GUI, another program or a browser running `sofia-web`) controls
   SOFIA at a time.
3. Press **Iniciar**. Each recognised gesture plays its motion, and between
   gestures the panel follows the hand. When the hand leaves the image, or
   detection pauses or stops, the panel holds its pose after 0.5 s.
4. **STOP** holds the pose and releases control, whoever has it. Unticking
   the box or closing the window also sends STOP if this GUI has control.

Rejected commands (for example a gesture while another is still playing)
appear in the status bar and the event log.

**From the terminal**, for tests and calibration:

```bash
python -m comms ping                # 4 PINGs to 192.168.4.1:4210
python -m comms status              # state, controller and the 32 joint angles
python -m comms arm                 # take control, power the servos
python -m comms gesture V           # INDEX, V, PALM, SWIPE_LEFT, SWIPE_RIGHT, GRAB
python -m comms target 0.8 0.5      # point at a camera point (0-1) for 1 s
python -m comms angles 10 -5 --module 3   # joint angles in degrees, for calibration
python -m comms stop                # hold the pose, release control
python -m comms off                 # PWM off
```

Every run uses local UDP port 4211, so consecutive runs count as the same
client. `gesture`, `target` and `angles` send ARM first.

## Files

- `gui/`: the PySide6 GUI. `bridge.py` adapts `DetectionWorker` to Qt signals, `maestro.py` does the same for `MaestroClient`, `video_view.py` draws the frames and `main_window.py` holds the layout and controls. Run with `python -m gui`.
- `comms/`: link to the ESP32 Maestro. `protocol.py` encodes and decodes frames and payloads, `link.py` sends one command and waits for its answer, `maestro.py` (`MaestroClient`) runs the link on a background thread for the GUI, and `python -m comms` is the command-line client.
- `tracker.py`: command-line front end. Shows the camera in an OpenCV window with the overlay (raw and smoothed palm centroid, posture, FPS).
- `detection.py`: the detection pipeline, independent of any front end. Camera capture, MediaPipe hand landmarks plus gesture recognition (one frame in, one `Detection` out), the overlay drawing, and `DetectionWorker`, which runs it all on a background thread that can be started, paused, resumed and stopped (used by the GUI).
- `events.py`: `Event` and `EventLog`, the session's CSV event log.
- `gestures.py`: posture classification, smoothing filters and the gesture recognizer. The tuning thresholds are at the top of the file.
- `docs/JUSTIFICACION.md`: design justification (Spanish).
- `docs/DETECCION.md`: detection rules and smoothing (Spanish). The evaluation protocol, scripts and results live on the `evaluation` branch.
- `docs/MANEJO_DE_ERRORES.md`: every logged event, when it happens and how it's handled (Spanish).
- `docs/INTERFAZ.md`: GUI architecture, threading model, wireframe, controls and framework justification (Spanish).
- `tests/`: pytest suite for the detection worker and the GUI.
- `find_camera.py`: previews each camera index so you can find your webcam.
- `requirements.txt`: Python dependencies. `requirements-dev.txt` adds the test tools.

## Troubleshooting

- **`pip install` can't find a version.** Remove the `==...` pins in
  `requirements.txt` and let pip choose compatible versions.
- **Gestures misfire or don't trigger.** Run with `--debug` and adjust the
  thresholds at the top of `gestures.py`. The main ones are
  `FINGER_EXT_RATIO` (when a finger counts as extended), `SWIPE_RATIO` (how
  far a swipe must travel) and `HOLD_TIME` (how long a static posture must
  be held).
