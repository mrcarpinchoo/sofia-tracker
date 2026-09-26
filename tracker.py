"""
SOFIA - Hand Tracking (Stage 2: posture recognition)
-----------------------------------------------------
Tracks a hand via the laptop webcam using MediaPipe's HandLandmarker
(current Tasks API). The gesture recognizer (gestures.py) smooths the
landmarks and the "palm centroid" with exponential moving averages (EMA)
to remove landmark jitter; here we draw both the raw and smoothed centroid.

On top of tracking, it recognizes the five SOFIA gestures (see
gestures.py) and shows the SOFIA motion each one will trigger.

Note: mediapipe deprecated the old `mp.solutions.hands` API back in 2023,
and recent pip releases (0.10.10+) removed it outright, so this uses the
replacement Tasks API instead.

This stage only visualizes tracking and gestures - no module/servo math yet.
Press 'q' to quit.
"""

import argparse
import os
import time
import urllib.request

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from gestures import SOFIA_ACTIONS, GestureRecognizer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CAM_INDEX = 0
MAX_HANDS = 1
DETECTION_CONF = 0.6
TRACKING_CONF = 0.6
BANNER_SECONDS = 1.5       # how long a recognized gesture stays on screen

MODEL_PATH = "hand_landmarker.task"
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


def ensure_model():
    """Download the hand landmarker model bundle if it's not present."""
    if not os.path.exists(MODEL_PATH):
        print(f"Downloading model to {MODEL_PATH} ...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("Done.")


def create_detector():
    """Build the MediaPipe HandLandmarker in VIDEO mode."""
    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.HandLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.VIDEO,
        num_hands=MAX_HANDS,
        min_hand_detection_confidence=DETECTION_CONF,
        min_tracking_confidence=TRACKING_CONF,
    )
    return mp_vision.HandLandmarker.create_from_options(options)


def draw_hand(frame, landmarks, w, h):
    pts_px = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, pts_px[a], pts_px[b], (255, 255, 255), 2)
    for x, y in pts_px:
        cv2.circle(frame, (x, y), 3, (255, 180, 0), -1)


def parse_args():
    parser = argparse.ArgumentParser(description="SOFIA hand tracker")
    parser.add_argument(
        "--camera", type=int, default=CAM_INDEX,
        help="Camera index to use (run find_camera.py if unsure)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Show per-finger extended flags (for threshold tuning)",
    )
    parser.add_argument(
        "--no-smooth", action="store_true",
        help="Disable landmark/centroid smoothing (for comparison)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    ensure_model()

    detector = create_detector()

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {args.camera}")
    print(f"Using camera index {args.camera}")

    recognizer = GestureRecognizer(smoothing=not args.no_smooth)
    banner, banner_until = None, 0.0
    start_time = time.time()
    fps, last_frame = 0.0, start_time

    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            print("Failed to read frame from camera.")
            break

        frame = cv2.flip(frame, 1)  # mirror for natural interaction
        h, w = frame.shape[:2]

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int((time.time() - start_time) * 1000)

        result = detector.detect_for_video(mp_image, timestamp_ms)

        if result.hand_landmarks:
            landmarks = result.hand_landmarks[0]  # first detected hand

            draw_hand(frame, landmarks, w, h)

            now = time.time()
            posture, event = recognizer.update(landmarks, now, w, h)

            raw_c, smooth_c = recognizer.raw_centroid, recognizer.centroid
            sx, sy = int(smooth_c[0]), int(smooth_c[1])

            # Draw raw (small, red) vs smoothed (large, green) point
            cv2.circle(frame, (int(raw_c[0]), int(raw_c[1])), 4, (0, 0, 255), -1)
            cv2.circle(frame, (sx, sy), 10, (0, 255, 0), -1)

            # Normalized coords (0-1) - useful later for panel mapping
            nx, ny = sx / w, sy / h
            cv2.putText(
                frame,
                f"target: px=({sx},{sy})  norm=({nx:.2f},{ny:.2f})",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )

            cv2.putText(
                frame, f"posture: {posture or '-'}", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2,
            )
            if event:
                banner = f"{event} -> SOFIA: {SOFIA_ACTIONS[event]}"
                banner_until = now + BANNER_SECONDS
                print(banner)

            if args.debug:
                flags = "  ".join(
                    f"{name}:{int(v)}" for name, v in recognizer.extended.items()
                )
                cv2.putText(
                    frame, flags, (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
                )
        else:
            recognizer.reset()  # also clears the smoothing filters
            cv2.putText(
                frame, "no hand detected", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2,
            )

        now = time.time()
        fps = 0.9 * fps + 0.1 / max(now - last_frame, 1e-6)
        last_frame = now
        cv2.putText(
            frame, f"{fps:.0f} FPS" + ("  (no smoothing)" if args.no_smooth else ""),
            (w - 230, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
        )

        if banner and now < banner_until:
            cv2.putText(
                frame, banner, (10, h - 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2,
            )

        cv2.imshow("SOFIA - Hand Tracking", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
