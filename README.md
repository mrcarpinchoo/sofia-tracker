# SOFIA Tracker

Webcam hand tracking and gesture recognition for SOFIA. It uses MediaPipe's
HandLandmarker to track one hand, smooths the landmarks and a palm-centroid
point, and recognizes five gestures (three static, two dynamic), each mapped
to a SOFIA motion.

At this stage, gestures are only shown on screen and printed to the console.
They aren't sent to the simulation or the servos yet.

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

Run the tracker:

```bash
python tracker.py                 # default camera (index 0)
python tracker.py --camera 1      # a specific camera
python tracker.py --debug         # also show which fingers are detected as extended
python tracker.py --no-smooth     # disable smoothing (for comparison)
```

Press `q` to quit. Type `deactivate` to leave the virtual environment.

### Experiments

Run a guided session per user and lighting condition (`bright`, `normal`,
`dim` or `backlight`). It asks for each gesture in random order, then
records 20 s of idle movement to count false positives:

```bash
python evaluate.py --user U1 --light dim                # smoothing on
python evaluate.py --user U1 --light dim --no-smooth    # baseline
python evaluate.py --user U1 --light dim --trials 10    # more repetitions
```

Each session writes a CSV to `results/`. Then generate the tables and the
confusion matrix:

```bash
python analyze.py     # writes results/summary.md and results/confusion.png
```

## Files

- `tracker.py`: command-line front end. Shows the camera in an OpenCV window with the overlay (raw and smoothed palm centroid, posture, FPS).
- `detection.py`: the detection pipeline, independent of any front end. Camera capture, MediaPipe hand landmarks plus gesture recognition (one frame in, one `Detection` out), the overlay drawing, and `DetectionWorker`, which runs it all on a background thread that can be started, paused, resumed and stopped (used by the GUI).
- `gestures.py`: posture classification, smoothing filters and the gesture recognizer. The tuning thresholds are at the top of the file.
- `evaluate.py`: guided test sessions that log one CSV row per trial.
- `analyze.py`: turns the CSVs in `results/` into `summary.md` and `confusion.png`.
- `results/`: experiment data (CSVs) and generated summaries.
- `docs/JUSTIFICACION.md`: design justification (Spanish).
- `docs/DETECCION_Y_EVALUACION.md`: detection rules, smoothing, evaluation protocol and results (Spanish).
- `find_camera.py`: previews each camera index so you can find your webcam.
- `requirements.txt`: Python dependencies.

## Troubleshooting

- **`pip install` can't find a version.** Remove the `==...` pins in
  `requirements.txt` and let pip choose compatible versions.
- **Gestures misfire or don't trigger.** Run with `--debug` and adjust the
  thresholds at the top of `gestures.py`. The main ones are
  `FINGER_EXT_RATIO` (when a finger counts as extended), `SWIPE_RATIO` (how
  far a swipe must travel) and `HOLD_TIME` (how long a static posture must
  be held).
