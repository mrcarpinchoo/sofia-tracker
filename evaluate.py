"""
SOFIA - Guided evaluation sessions
----------------------------------
Runs a shuffled list of gesture trials in front of the webcam and logs one
CSV row per trial (plus one IDLE row measuring false positives).

    python evaluate.py --user U1 --light dim [--camera 0] [--trials 5]
                       [--no-smooth] [--out results]

Each trial shows "Get ready: <gesture>" for READY_SECONDS, then records for
RECORD_SECONDS. Press SPACE to start the session, 'q' to abort (rows already
logged are kept). Use analyze.py to turn the CSVs into tables and plots.
"""

import argparse
import csv
import os
import random
import time
from datetime import datetime

import cv2
import mediapipe as mp

from gestures import GestureRecognizer
from tracker import CAM_INDEX, create_detector, draw_hand, ensure_model

LIGHTS = ("bright", "normal", "dim", "backlight")
GESTURES = ("INDEX", "V", "PALM", "SWIPE_LEFT", "SWIPE_RIGHT", "GRAB")
HINTS = {
    "INDEX": "hold up your index finger, still",
    "V": "hold up index + middle finger (V), still",
    "PALM": "show your open palm and hold it still",
    "SWIPE_LEFT": "sweep your open hand quickly to the LEFT",
    "SWIPE_RIGHT": "sweep your open hand quickly to the RIGHT",
    "GRAB": "open palm, then close it into a fist",
}

READY_SECONDS = 2.0
RECORD_SECONDS = 4.0
IDLE_SECONDS = 20.0

COLUMNS = [
    "timestamp", "user", "light", "smoothing", "trial", "expected", "detected",
    "correct", "latency_s", "extra_events", "duration_s", "detect_rate",
    "brightness", "fps", "proc_ms",
]

WINDOW = "SOFIA - Evaluation"


def parse_args():
    p = argparse.ArgumentParser(description="SOFIA guided evaluation")
    p.add_argument("--user", required=True, help="User id, e.g. U1")
    p.add_argument("--light", required=True, choices=LIGHTS)
    p.add_argument("--camera", type=int, default=CAM_INDEX)
    p.add_argument("--trials", type=int, default=5, help="Repetitions per gesture")
    p.add_argument("--no-smooth", action="store_true",
                   help="Disable smoothing (baseline for comparison)")
    p.add_argument("--out", default="results", help="Output directory")
    return p.parse_args()


def put(frame, text, y, scale=0.7, color=(255, 255, 255)):
    cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)


class Session:
    """Owns the camera, detector and recognizer; steps one frame at a time."""

    def __init__(self, args):
        self.detector = create_detector()
        self.cap = cv2.VideoCapture(args.camera)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open camera index {args.camera}")
        self.recognizer = GestureRecognizer(smoothing=not args.no_smooth)
        self.start = time.time()

    def step(self):
        """Grab + process one frame. Returns a dict with the frame and stats."""
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("Failed to read frame from camera")
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]

        t0 = time.perf_counter()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ts_ms = int((time.time() - self.start) * 1000)
        result = self.detector.detect_for_video(mp_image, ts_ms)

        now = time.time()
        event, has_hand = None, bool(result.hand_landmarks)
        if has_hand:
            landmarks = result.hand_landmarks[0]
            _, event = self.recognizer.update(landmarks, now, w, h)
            draw_hand(frame, landmarks, w, h)
        else:
            self.recognizer.reset()
        proc_ms = (time.perf_counter() - t0) * 1000

        return {
            "frame": frame, "now": now, "event": event, "has_hand": has_hand,
            "proc_ms": proc_ms,
            "brightness": float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()),
        }

    def close(self):
        self.cap.release()
        cv2.destroyAllWindows()


def show(frame):
    """Display the frame; returns False if the user pressed 'q'."""
    cv2.imshow(WINDOW, frame)
    return cv2.waitKey(1) & 0xFF != ord("q")


def wait_for_space(sess, message):
    while True:
        s = sess.step()
        put(s["frame"], message, 40)
        put(s["frame"], "SPACE = start   q = quit", 75, 0.6)
        cv2.imshow(WINDOW, s["frame"])
        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            return True
        if key == ord("q"):
            return False


def run_segment(sess, expected, duration, header, ready=READY_SECONDS):
    """Ready countdown, then record. Returns stats dict or None if aborted."""
    end = time.time() + ready
    while time.time() < end:
        s = sess.step()
        put(s["frame"], header, 30, 0.6)
        put(s["frame"], f"Get ready: {expected}  ({end - time.time():.0f})", 65,
            0.9, (0, 200, 255))
        if expected in HINTS:
            put(s["frame"], HINTS[expected], 95, 0.6, (0, 200, 255))
        if not show(s["frame"]):
            return None

    sess.recognizer.reset()
    events, frames, hand_frames = [], 0, 0
    procs, brights = [], []
    t_start = time.time()
    while time.time() - t_start < duration:
        s = sess.step()
        frames += 1
        hand_frames += s["has_hand"]
        procs.append(s["proc_ms"])
        brights.append(s["brightness"])
        if s["event"]:
            events.append((s["now"] - t_start, s["event"]))
        put(s["frame"], header, 30, 0.6)
        put(s["frame"], f"REC {expected}  {duration - (time.time() - t_start):.1f}s",
            65, 0.9, (0, 0, 255))
        if events:
            put(s["frame"], f"detected: {events[-1][1]}", 95, 0.7, (0, 255, 0))
        if not show(s["frame"]):
            return None

    elapsed = time.time() - t_start
    return {
        "events": events,
        "duration": elapsed,
        "detect_rate": hand_frames / max(frames, 1),
        "brightness": sum(brights) / max(len(brights), 1),
        "fps": frames / max(elapsed, 1e-6),
        "proc_ms": sum(procs) / max(len(procs), 1),
    }


def make_row(args, trial, expected, seg):
    events = seg["events"]
    detected = events[0][1] if events else "NONE"
    if expected == "IDLE":
        correct = int(not events)      # any event while idle is a false positive
        latency = ""
    else:
        correct = int(detected == expected)
        latency = f"{events[0][0]:.3f}" if events else ""
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "user": args.user,
        "light": args.light,
        "smoothing": "nosmooth" if args.no_smooth else "smooth",
        "trial": trial,
        "expected": expected,
        "detected": detected,
        "correct": correct,
        "latency_s": latency,
        "extra_events": "|".join(e for _, e in events[1:]),
        "duration_s": f"{seg['duration']:.2f}",
        "detect_rate": f"{seg['detect_rate']:.3f}",
        "brightness": f"{seg['brightness']:.1f}",
        "fps": f"{seg['fps']:.1f}",
        "proc_ms": f"{seg['proc_ms']:.1f}",
    }


def main():
    args = parse_args()
    ensure_model()
    os.makedirs(args.out, exist_ok=True)

    tag = "nosmooth" if args.no_smooth else "smooth"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(args.out, f"{args.user}_{args.light}_{tag}_{stamp}.csv")

    trials = [g for g in GESTURES for _ in range(args.trials)]
    random.shuffle(trials)
    total = len(trials)

    sess = Session(args)
    print(f"Logging to {path}")
    try:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()

            msg = f"{args.user} / {args.light} / {tag}: {total} trials + idle"
            if not wait_for_space(sess, msg):
                return

            done = correct = 0
            for i, expected in enumerate(trials, 1):
                seg = run_segment(sess, expected, RECORD_SECONDS,
                                  f"Trial {i}/{total}")
                if seg is None:
                    print("Aborted.")
                    break
                row = make_row(args, i, expected, seg)
                writer.writerow(row)
                f.flush()
                done += 1
                correct += row["correct"]
                print(f"[{i}/{total}] expected={expected:12s} "
                      f"detected={row['detected']:12s} "
                      f"{'OK' if row['correct'] else 'MISS'}  "
                      f"latency={row['latency_s'] or '-'}s")
            else:
                seg = run_segment(
                    sess, "IDLE", IDLE_SECONDS,
                    "Idle: move your hand naturally, NO gestures", ready=3.0,
                )
                if seg is not None:
                    row = make_row(args, "idle", "IDLE", seg)
                    writer.writerow(row)
                    f.flush()
                    fp = len(seg["events"])
                    print(f"[idle] {fp} false positive(s) in {seg['duration']:.0f}s")

            if done:
                print(f"Accuracy: {correct}/{done} = {correct / done:.0%}")
    finally:
        sess.close()


if __name__ == "__main__":
    main()
