"""
SOFIA - Posture / gesture recognition
-------------------------------------
Classifies MediaPipe hand landmarks into the five SOFIA gestures:

  static  : INDEX (look up/down), V (horizontal 8)
  dynamic : WAVE (open palm waving side to side -> look right/left),
            SWIPE_LEFT / SWIPE_RIGHT (sweeping wave), GRAB (open -> fist)

PALM (all five fingers extended) is still a posture *label*, because WAVE
and GRAB depend on it, but it no longer fires an event by itself.

Smoothing happens at three levels: an EMA over the 21 landmarks (before
finger classification), an EMA over the palm centroid (for the motion
rules) and a majority vote over the last frames (for the posture label).
All distances are measured in pixels and compared against the palm width,
so thresholds don't depend on how far the hand is from the camera.
"""

from collections import Counter, deque

import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FINGER_EXT_RATIO = 1.15    # tip must be this much farther from wrist than PIP
THUMB_EXT_RATIO = 1.1      # thumb tip vs IP distance to pinky MCP
DEBOUNCE_FRAMES = 5        # majority vote window for the posture label

LANDMARK_ALPHA = 0.5       # EMA on the 21 landmarks (1 = no smoothing)
CENTROID_ALPHA = 0.35      # EMA on the palm centroid (1 = no smoothing)

HOLD_TIME = 0.5            # s a static posture must be held
STILL_RATIO = 0.3          # max displacement during hold (x palm width)
STATIC_COOLDOWN = 2.0      # s before the same static posture can re-fire

SWIPE_WINDOW = 0.4         # s
SWIPE_RATIO = 2.5          # min |dx| (x palm width)
SWIPE_COOLDOWN = 1.0       # s

WAVE_WINDOW = 1.5          # s: PALM must hold for the whole window
WAVE_MIN_SPAN = 0.75       # s of history required before WAVE can fire
WAVE_REVERSALS = 2         # min direction changes of the centroid x
WAVE_AMP_RATIO = 0.6       # hysteresis: min stroke (x palm width)
WAVE_HORIZ_RATIO = 2.0     # x-range must exceed this x the y-range
WAVE_COOLDOWN = 1.5        # s

GRAB_WINDOW = 0.6          # s: PALM must have been seen this recently
GRAB_FIST_FRAMES = 3       # consecutive raw FIST frames
GRAB_COOLDOWN = 1.0        # s

HISTORY_SECONDS = 1.5      # must be >= WAVE_WINDOW

WRIST = 0
FINGERS = {                # name: (tip, pip)
    "index": (8, 6),
    "middle": (12, 10),
    "ring": (16, 14),
    "pinky": (20, 18),
}
THUMB_TIP, THUMB_IP = 4, 3
INDEX_MCP, PINKY_MCP = 5, 17
PALM_LANDMARK_IDS = [0, 5, 9, 17]

STATIC_POSTURES = ("INDEX", "V")

SOFIA_ACTIONS = {
    "INDEX": "look up, then down",
    "V": "draw a horizontal 8",
    "WAVE": "look right, then left",
    "SWIPE_LEFT": "sweeping wave to the left",
    "SWIPE_RIGHT": "sweeping wave to the right",
    "GRAB": "flinch / return to rest",
}


class EMAFilter:
    """Exponential moving average: s_t = a*x_t + (1-a)*s_{t-1}.

    Works on numpy arrays of any shape (a 2D point or all 21 landmarks).
    """

    def __init__(self, alpha: float):
        self.alpha = alpha
        self.value = None

    def update(self, new: np.ndarray) -> np.ndarray:
        if self.value is None:
            self.value = new
        else:
            self.value = self.alpha * new + (1 - self.alpha) * self.value
        return self.value

    def reset(self):
        self.value = None


def _to_px(landmarks, w, h) -> np.ndarray:
    return np.array([(lm.x * w, lm.y * h) for lm in landmarks])


def _dist(a, b) -> float:
    return float(np.linalg.norm(a - b))


def finger_extended(pts: np.ndarray) -> dict:
    """Per-finger extended flags from pixel landmarks (rotation invariant)."""
    ext = {
        name: _dist(pts[WRIST], pts[tip]) > _dist(pts[WRIST], pts[pip]) * FINGER_EXT_RATIO
        for name, (tip, pip) in FINGERS.items()
    }
    ext["thumb"] = (
        _dist(pts[THUMB_TIP], pts[PINKY_MCP])
        > _dist(pts[THUMB_IP], pts[PINKY_MCP]) * THUMB_EXT_RATIO
    )
    return ext


def classify_posture(ext: dict):
    """Map finger flags to INDEX / V / PALM / FIST, or None if unknown."""
    i, m, r, p, t = ext["index"], ext["middle"], ext["ring"], ext["pinky"], ext["thumb"]
    if i and m and r and p and t:
        return "PALM"
    if not (i or m or r or p):
        return "FIST"
    if i and not (m or r or p):
        return "INDEX"
    if i and m and not (r or p):
        return "V"
    return None


def _count_reversals(xs, min_amp: float) -> int:
    """Count direction changes of a 1D signal, ignoring strokes < min_amp.

    A reversal only counts once the signal has moved back more than
    `min_amp` from the last extreme (hysteresis), so jitter is ignored.
    """
    direction = 0            # +1 rising, -1 falling, 0 not yet decided
    extreme = xs[0]
    count = 0
    for x in xs[1:]:
        if direction == 0:
            if abs(x - extreme) > min_amp:
                direction = 1 if x > extreme else -1
                extreme = x
        elif direction > 0:
            if x > extreme:
                extreme = x
            elif extreme - x > min_amp:
                direction, extreme = -1, x
                count += 1
        else:
            if x < extreme:
                extreme = x
            elif x - extreme > min_amp:
                direction, extreme = 1, x
                count += 1
    return count


class GestureRecognizer:
    """Turns a stream of landmarks into (posture, event) pairs."""

    def __init__(self, smoothing: bool = True):
        self.smoothing = smoothing
        self._lm_filter = EMAFilter(LANDMARK_ALPHA if smoothing else 1.0)
        self._c_filter = EMAFilter(CENTROID_ALPHA if smoothing else 1.0)
        self.reset()

    def reset(self):
        self.history = deque()   # (t, centroid_px, posture, palm_width)
        self.raw = deque(maxlen=DEBOUNCE_FRAMES)
        self.extended = None
        self.raw_centroid = None   # unfiltered palm centroid (px)
        self.centroid = None       # smoothed palm centroid (px)
        self.blocked_until = 0.0
        self.static_armed = True
        self.last_static = None
        self.last_static_time = -1e9
        self._lm_filter.reset()
        self._c_filter.reset()

    def _debounced(self):
        label, count = Counter(self.raw).most_common(1)[0]
        return label if count > len(self.raw) // 2 else None

    def _fire(self, event, t, cooldown):
        self.blocked_until = t + cooldown
        self.history.clear()
        return event

    def update(self, landmarks, t, w, h):
        raw_pts = _to_px(landmarks, w, h)
        pts = self._lm_filter.update(raw_pts)
        self.extended = finger_extended(pts)
        raw_posture = classify_posture(self.extended)
        self.raw.append(raw_posture)
        posture = self._debounced()

        self.raw_centroid = raw_pts[PALM_LANDMARK_IDS].mean(axis=0)
        centroid = self._c_filter.update(self.raw_centroid)
        self.centroid = centroid
        palm_w = max(_dist(pts[INDEX_MCP], pts[PINKY_MCP]), 1.0)

        self.history.append((t, centroid, posture, palm_w))
        while self.history and t - self.history[0][0] > HISTORY_SECONDS:
            self.history.popleft()

        if posture != self.last_static:
            self.static_armed = True

        if t < self.blocked_until:
            return posture, None

        # Swipe: large, mostly horizontal displacement in a short window
        window = [e for e in self.history if t - e[0] <= SWIPE_WINDOW]
        dx, dy = centroid - window[0][1]
        if abs(dx) > SWIPE_RATIO * palm_w and abs(dx) > 2 * abs(dy):
            event = "SWIPE_RIGHT" if dx > 0 else "SWIPE_LEFT"
            return posture, self._fire(event, t, SWIPE_COOLDOWN)

        # Wave: open palm all along the window, oscillating horizontally
        wave = [e for e in self.history if t - e[0] <= WAVE_WINDOW]
        if (
            t - wave[0][0] >= WAVE_MIN_SPAN
            and all(e[2] == "PALM" for e in wave)
        ):
            xs = [e[1][0] for e in wave]
            ys = [e[1][1] for e in wave]
            horizontal = (max(xs) - min(xs)) > WAVE_HORIZ_RATIO * (max(ys) - min(ys))
            if horizontal and _count_reversals(xs, WAVE_AMP_RATIO * palm_w) >= WAVE_REVERSALS:
                return posture, self._fire("WAVE", t, WAVE_COOLDOWN)

        # Grab: recent open palm, now a sustained fist
        fist_now = (
            len(self.raw) >= GRAB_FIST_FRAMES
            and all(p == "FIST" for p in list(self.raw)[-GRAB_FIST_FRAMES:])
        )
        palm_recent = any(
            e[2] == "PALM" for e in self.history if t - e[0] <= GRAB_WINDOW
        )
        if fist_now and palm_recent:
            return posture, self._fire("GRAB", t, GRAB_COOLDOWN)

        # Static: same posture held still for HOLD_TIME
        if posture in STATIC_POSTURES and self.static_armed:
            hold = [e for e in self.history if t - e[0] <= HOLD_TIME]
            spans_hold = t - self.history[0][0] >= HOLD_TIME
            same = all(e[2] == posture for e in hold)
            still = all(
                _dist(centroid, e[1]) < STILL_RATIO * palm_w for e in hold
            )
            cooled = (
                posture != self.last_static
                or t - self.last_static_time >= STATIC_COOLDOWN
            )
            if spans_hold and same and still and cooled:
                self.static_armed = False
                self.last_static = posture
                self.last_static_time = t
                return posture, posture

        return posture, None
