# SOFIA Tracker

Webcam hand tracking and gesture recognition for SOFIA. It uses MediaPipe's
HandLandmarker to track one hand, smooths a palm-centroid point, and
recognizes five gestures, each mapped to a SOFIA motion.

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

Static postures fire after you hold the hand **still** for about 0.5 s, so a
swipe with an open palm doesn't also trigger "open palm". Holding a posture
fires it only once.

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
```

Press `q` to quit. Type `deactivate` to leave the virtual environment.

## Files

- `tracker.py`: main loop. Captures the camera, detects the hand, smooths the palm centroid and draws the overlay.
- `gestures.py`: posture classification and the gesture recognizer. The tuning thresholds are at the top of the file.
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
