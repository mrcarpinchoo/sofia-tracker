"""
SOFIA - Hand Tracking (Stage 2: posture recognition)
-----------------------------------------------------
Command-line front end: shows the webcam with the detection overlay in an
OpenCV window. Detection lives in detection.py and gesture recognition in
gestures.py.

The recognizer smooths the landmarks and the "palm centroid" with
exponential moving averages (EMA) to remove landmark jitter; here we draw
both the raw and smoothed centroid. On top of tracking, it recognizes the
five SOFIA gestures and shows the SOFIA motion each one will trigger.

This stage only visualizes tracking and gestures - no module/servo math yet.
Press 'q' to quit.
"""

import argparse
import logging
import time

import cv2

from detection import (
    CAM_INDEX,
    Camera,
    CameraError,
    Detection,
    DetectionPipeline,
    FpsMeter,
    draw_detection,
)
from gestures import SOFIA_ACTIONS

BANNER_SECONDS = 1.5       # how long a recognized gesture stays on screen
WINDOW = "SOFIA - Hand Tracking"


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


def put_text(frame, text, origin, color, scale=0.6, thickness=2):
    cv2.putText(
        frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness,
    )


def draw_status(frame, detection: Detection, debug: bool):
    """Target coordinates, posture and (optionally) per-finger flags."""
    if not detection.hand_found:
        put_text(frame, "no hand detected", (10, 30), (0, 0, 255))
        return

    sx, sy = detection.centroid.astype(int)
    nx, ny = detection.target_norm
    put_text(
        frame, f"target: px=({sx},{sy})  norm=({nx:.2f},{ny:.2f})",
        (10, 30), (0, 255, 0),
    )
    put_text(frame, f"posture: {detection.posture or '-'}", (10, 60), (255, 255, 0))

    if debug:
        flags = "  ".join(
            f"{name}:{int(v)}" for name, v in detection.extended.items()
        )
        put_text(frame, flags, (10, 90), (200, 200, 200), scale=0.5, thickness=1)


def run(camera: Camera, pipeline: DetectionPipeline, debug: bool):
    """Show frames until the user presses 'q'."""
    fps = FpsMeter()
    smoothing_note = "" if pipeline.smoothing else "  (no smoothing)"
    banner, banner_until = None, 0.0

    while True:
        frame = camera.read()
        h, w = frame.shape[:2]

        detection = pipeline.process(frame)
        draw_detection(frame, detection)
        draw_status(frame, detection, debug)

        if detection.event:
            banner = f"{detection.event} -> SOFIA: {SOFIA_ACTIONS[detection.event]}"
            banner_until = detection.timestamp + BANNER_SECONDS
            print(banner)

        put_text(
            frame, f"{fps.tick():.0f} FPS{smoothing_note}", (w - 230, 30),
            (255, 255, 255),
        )
        if banner and time.monotonic() < banner_until:
            put_text(frame, banner, (10, h - 20), (0, 200, 255), scale=0.8)

        cv2.imshow(WINDOW, frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            return


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        with (
            Camera(args.camera) as camera,
            DetectionPipeline(smoothing=not args.no_smooth) as pipeline,
        ):
            print(f"Using camera index {args.camera}")
            run(camera, pipeline, args.debug)
    except CameraError as exc:
        raise SystemExit(exc)
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
