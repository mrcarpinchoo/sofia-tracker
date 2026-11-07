"""
Background client for the SOFIA Maestro, for long-running programs (the GUI).

Commands go out one at a time (stop-and-wait, PROTOCOL.md §8) from a worker
thread, in priority order: STOP/OFF, then ARM and gestures, then the newest
hand target (a stream: stale targets are dropped, never retransmitted), then
a status poll every 250 ms. The poll also supervises the link (§8.4) and
keeps this client in control while it holds it (§8.5).

The methods are safe to call from any thread. Callbacks run on the worker
thread; a GUI must hand their data over to its own thread.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from comms.link import MAX_RETRIES, Reply, Session, error_name
from comms.protocol import (
    Command,
    Gesture,
    Status,
    gesture_payload,
    parse_status_ack,
    target_payload,
)

TARGET_INTERVAL_S = 0.04   # 25 Hz
STATUS_INTERVAL_S = 0.25
LOST_AFTER = 3             # consecutive commands without a response
LATENCY_SAMPLES = 500


@dataclass(frozen=True)
class LinkInfo:
    online: bool                 # the last commands were answered
    status: Status | None
    p95_ms: float | None         # 95th percentile command-to-response time
    within_500: float | None     # share of commands answered within 500 ms


@dataclass(frozen=True)
class _Outgoing:
    name: str
    command: Command
    payload: bytes = b""
    retry: bool = True           # retransmit on timeout
    user: bool = True            # report a NACK or a lost command


class MaestroClient:
    def __init__(self, host: str, port: int,
                 on_info: Callable[[LinkInfo], None],
                 on_rejected: Callable[[str, str], None]):
        self._session = Session(host, port)
        self._on_info = on_info
        self._on_rejected = on_rejected
        self._cv = threading.Condition()
        self._urgent: deque[_Outgoing] = deque()
        self._queue: deque[_Outgoing] = deque()
        self._target: tuple[float, float] | None = None
        self._last_target = 0.0
        self._next_status = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._failures = 0
        self._latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)
        self._status: Status | None = None

    # Commands (any thread) -------------------------------------------------
    def arm(self):
        self._push(self._queue, _Outgoing("ARM", Command.ARM))

    def stop(self):
        """Jumps ahead of everything else waiting."""
        with self._cv:
            self._target = None
            self._queue.clear()
        self._push(self._urgent, _Outgoing("STOP", Command.STOP))

    def off(self):
        with self._cv:
            self._target = None
            self._queue.clear()
        self._push(self._urgent, _Outgoing("OFF", Command.OFF))

    def gesture(self, gesture: Gesture):
        self._push(self._queue, _Outgoing(
            f"GESTURE {gesture.name}", Command.GESTURE, gesture_payload(gesture)))

    def set_target(self, x: float, y: float):
        """Camera point, 0-1 (mirrored, y down). Sent at 25 Hz until cleared."""
        with self._cv:
            self._target = (x, y)
            self._cv.notify()

    def clear_target(self):
        with self._cv:
            self._target = None

    @property
    def is_controller(self) -> bool:
        return bool(self._status and self._status.is_controller)

    # Lifecycle -------------------------------------------------------------
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, name="maestro", daemon=True)
        self._thread.start()

    def close(self, stop_if_controller: bool = True):
        """Stop the worker. If this client controls SOFIA, send STOP first
        so the pose is held and control is released at once."""
        if stop_if_controller and self.is_controller:
            self.stop()
            deadline = time.monotonic() + MAX_RETRIES * 0.25
            while self._urgent and time.monotonic() < deadline:
                time.sleep(0.01)
        with self._cv:
            self._running = False
            self._cv.notify()
        if self._thread:
            self._thread.join(timeout=2)
        self._session.close()

    # Worker thread ---------------------------------------------------------
    def _push(self, queue: deque, out: _Outgoing):
        with self._cv:
            queue.append(out)
            self._cv.notify()

    def _next(self) -> _Outgoing | None:
        """Wait for the next command to send, or None to stop."""
        with self._cv:
            while self._running:
                if self._urgent:
                    return self._urgent.popleft()
                if self._queue:
                    return self._queue.popleft()
                now = time.monotonic()
                # The periodic message that has been due the longest goes first.
                due_status = self._next_status
                due_target = self._last_target + TARGET_INTERVAL_S if self._target else None
                if due_target is not None and due_target <= min(now, due_status):
                    self._last_target = now
                    return _Outgoing("SET_TARGET", Command.SET_TARGET,
                                     target_payload(*self._target), retry=False)
                if due_status <= now:
                    self._next_status = now + STATUS_INTERVAL_S
                    return _Outgoing("GET_STATUS", Command.GET_STATUS, retry=False, user=False)
                wake = min(due_status, due_target) if due_target is not None else due_status
                self._cv.wait(timeout=max(0.0, wake - now))
        return None

    def _run(self):
        while (out := self._next()) is not None:
            try:
                reply = self._session.send(out.command, out.payload,
                                           MAX_RETRIES if out.retry else 0)
            except OSError as exc:
                reply = None
                self._on_rejected(out.name, f"error de red: {exc}")
                time.sleep(0.5)
            self._handle(out, reply)

    def _handle(self, out: _Outgoing, reply: Reply | None):
        if reply is None:
            self._latencies.append(float("inf"))
            self._failures += 1
            if out.user and out.command is not Command.SET_TARGET:
                self._on_rejected(out.name, "sin respuesta del Maestro")
            if self._failures == LOST_AFTER:
                self._status = None
                self._publish()
            return

        self._latencies.append(reply.rtt_ms + reply.retries * 200)
        self._failures = 0
        if not reply.ok:
            if out.user:
                self._on_rejected(out.name, error_name(reply.frame.payload))
            if out.command is Command.SET_TARGET:
                self.clear_target()   # e.g. NOT_CONTROLLER: stop streaming
        elif out.command is Command.GET_STATUS:
            self._status = parse_status_ack(reply.frame.payload)
            self._publish()

    def _publish(self):
        samples = sorted(self._latencies)
        n = len(samples)
        p95 = samples[min(n - 1, -(-95 * n // 100) - 1)] if n else None
        within = sum(ms <= 500 for ms in samples) / n if n else None
        self._on_info(LinkInfo(
            online=self._failures < LOST_AFTER, status=self._status,
            p95_ms=p95, within_500=within,
        ))
