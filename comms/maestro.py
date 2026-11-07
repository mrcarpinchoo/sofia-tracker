"""
Background client for the SOFIA Maestro, for long-running programs (the GUI).

Commands go out one at a time (stop-and-wait, PROTOCOL.md §8) from a worker
thread, in priority order: STOP/OFF, then ARM and gestures, then the newest
hand target (a stream: stale targets are dropped, never retransmitted), then
a status poll every 250 ms. The poll also supervises the link (§8.4) and
keeps this client in control while it holds it (§8.5).

Recovery:
- After 3 commands in a row without a response the link counts as lost; the
  status poll keeps running, and the first response restores it.
- If the user had taken control (ARM), ARM is sent again as soon as the link
  comes back, ahead of any gesture: the Maestro releases control after 3 s
  of silence, or it may have rebooted. From the controller, ARM changes
  nothing. Not after an outage longer than RESUME_WITHIN_S, and not once
  the user sent STOP/OFF or control was taken away while the link was up
  (another client's STOP): STOP keeps its top priority.
- A STOP/OFF without a response is sent again until the Maestro confirms it.
- A gesture is only worth playing right away: one waiting is replaced by a
  newer one, and gestures are dropped while the link is lost instead of
  being played late when it comes back.
- An error in the worker is reported and the worker carries on.

The methods are safe to call from any thread. Callbacks run on the worker
thread; a GUI must hand their data over to its own thread.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from comms.link import MAX_RETRIES, TIMEOUT_S, Reply, Session, error_name
from comms.protocol import (
    Command,
    ErrorCode,
    Gesture,
    Status,
    gesture_payload,
    parse_status_ack,
    target_payload,
)
from events import Event, Level

log = logging.getLogger(__name__)

TARGET_INTERVAL_S = 0.04   # 25 Hz
STATUS_INTERVAL_S = 0.25
LOST_AFTER = 3             # consecutive commands without a response
LATENCY_SAMPLES = 500
RESUME_WITHIN_S = 30.0     # take control back only after shorter outages
NETWORK_ERROR_PAUSE_S = 0.5

SOURCE = "maestro"


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
    user: bool = True            # report the result
    automatic: bool = False      # sent by the recovery logic, not the user


class MaestroClient:
    def __init__(self, host: str, port: int,
                 on_info: Callable[[LinkInfo], None],
                 on_event: Callable[[Event], None]):
        self._session = Session(host, port)
        self._on_info = on_info
        self._on_event = on_event
        self._cv = threading.Condition()
        self._urgent: deque[_Outgoing] = deque()
        self._queue: deque[_Outgoing] = deque()
        self._target: tuple[float, float] | None = None
        self._last_target = 0.0
        self._next_status = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)
        self._status: Status | None = None

        # Worker thread only, except _wants_control (under _cv)
        self._failures = 0
        self._first_failure_at = 0.0
        self._lost_since: float | None = None    # link counted as lost
        self._network_error = False              # reported, not yet cleared
        self._internal_error = ""                # last one reported
        self._wants_control = False              # the user armed and hasn't stopped
        self._unconfirmed_stop = False           # a STOP/OFF is being resent

    # Commands (any thread) -------------------------------------------------
    def arm(self):
        with self._cv:
            self._wants_control = True
        self._push(self._queue, _Outgoing("ARM", Command.ARM))

    def stop(self):
        """Jumps ahead of everything else waiting."""
        self._push_stop(_Outgoing("STOP", Command.STOP))

    def off(self):
        self._push_stop(_Outgoing("OFF", Command.OFF))

    def gesture(self, gesture: Gesture):
        out = _Outgoing(f"GESTURE {gesture.name}", Command.GESTURE, gesture_payload(gesture))
        with self._cv:
            stale = [o for o in self._queue if o.command is Command.GESTURE]
            for o in stale:
                self._queue.remove(o)
            self._queue.append(out)
            self._cv.notify()
        for o in stale:
            self._emit(Level.INFO, "GESTO_DESCARTADO",
                       f"{o.name} no se había enviado aún", f"se envía {out.name} en su lugar")

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
            deadline = time.monotonic() + (MAX_RETRIES + 1) * TIMEOUT_S + 0.1
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

    def _push_stop(self, out: _Outgoing):
        with self._cv:
            self._wants_control = False
            self._target = None
            self._queue.clear()
            # One STOP/OFF waiting is enough
            if not any(o.command is out.command for o in self._urgent):
                self._urgent.append(out)
            self._cv.notify()

    def _next(self) -> _Outgoing | None:
        """Wait for the next command to send, or None to stop."""
        with self._cv:
            while self._running:
                if self._urgent:
                    return self._urgent[0]   # removed once answered (_handle)
                if self._queue:
                    return self._queue.popleft()
                now = time.monotonic()
                # The periodic message that has been due the longest goes first.
                due_status = self._next_status
                due_target = self._last_target + TARGET_INTERVAL_S if self._target else None
                if due_target is not None and due_target <= min(now, due_status):
                    self._last_target = now
                    # A stream: a NACK (no control yet) is expected, not reported
                    return _Outgoing("SET_TARGET", Command.SET_TARGET,
                                     target_payload(*self._target), retry=False, user=False)
                if due_status <= now:
                    self._next_status = now + STATUS_INTERVAL_S
                    return _Outgoing("GET_STATUS", Command.GET_STATUS, retry=False, user=False)
                wake = min(due_status, due_target) if due_target is not None else due_status
                self._cv.wait(timeout=max(0.0, wake - now))
        return None

    def _run(self):
        while (out := self._next()) is not None:
            if out.command is Command.GESTURE and self._lost_since is not None:
                self._emit(Level.WARNING, "GESTO_DESCARTADO", f"{out.name}: enlace perdido",
                           "no se envía para no reproducirlo tarde al reconectar")
                continue
            try:
                reply = self._send(out)
                self._handle(out, reply)
                self._internal_error = ""
            except Exception as exc:
                # Never let one bad reply end the link; report each new error once
                detail = f"{out.name}: {type(exc).__name__}: {exc}"
                if detail != self._internal_error:
                    self._internal_error = detail
                    log.exception("Maestro worker error")
                    self._emit(Level.ERROR, "ERROR_INTERNO", detail,
                               "se descarta esa respuesta y el enlace continúa")
                self._finish_urgent(out)
                time.sleep(NETWORK_ERROR_PAUSE_S)

    def _send(self, out: _Outgoing) -> Reply | None:
        try:
            reply = self._session.send(out.command, out.payload,
                                       MAX_RETRIES if out.retry else 0)
        except OSError as exc:
            # E.g. the PC left the SOFIA-Maestro Wi-Fi: report once
            if not self._network_error:
                self._network_error = True
                self._emit(Level.ERROR, "ERROR_RED", f"{out.name}: {exc}",
                           f"se reintenta cada {NETWORK_ERROR_PAUSE_S:g} s; "
                           "revisar la conexión a la red SOFIA-Maestro")
            time.sleep(NETWORK_ERROR_PAUSE_S)
            return None
        self._network_error = False
        return reply

    def _handle(self, out: _Outgoing, reply: Reply | None):
        if reply is None:
            self._on_no_reply(out)
            return

        self._latencies.append(reply.rtt_ms + reply.retries * TIMEOUT_S * 1000)
        self._on_reply()
        self._finish_urgent(out)

        if out.command in (Command.STOP, Command.OFF) and self._unconfirmed_stop:
            self._unconfirmed_stop = False
            self._emit(Level.INFO, f"{out.name}_CONFIRMADO",
                       "el Maestro respondió al reenvío", "")

        if not reply.ok:
            self._on_nack(out, reply)
        elif out.command is Command.GET_STATUS:
            self._on_status(parse_status_ack(reply.frame.payload))
        elif out.user:
            if out.automatic:
                self._emit(Level.INFO, "CONTROL_RETOMADO",
                           "ARM aceptado tras restablecerse el enlace",
                           "los gestos y la mano vuelven a mover el panal")
            else:
                retries = f", {reply.retries} reenvíos" if reply.retries else ""
                self._emit(Level.INFO, "COMANDO_ACEPTADO",
                           f"{out.name} ({reply.rtt_ms:.0f} ms{retries})", "")

    def _on_no_reply(self, out: _Outgoing):
        self._latencies.append(float("inf"))
        if self._failures == 0:
            self._first_failure_at = time.monotonic()
        self._failures += 1

        if out.command in (Command.STOP, Command.OFF):
            # Stays first in _urgent and goes out again
            if not self._unconfirmed_stop:
                self._unconfirmed_stop = True
                self._emit(Level.ERROR, f"{out.name}_SIN_RESPUESTA",
                           f"{out.name} sin ACK tras {MAX_RETRIES} reenvíos",
                           f"se reenvía {out.name} hasta que el Maestro responda")
        elif out.user:
            self._emit(Level.WARNING, "COMANDO_SIN_RESPUESTA",
                       f"{out.name} sin ACK tras {MAX_RETRIES} reenvíos",
                       "el comando se descarta" if self._lost_since is None
                       else "el comando se descarta (enlace perdido)")

        if self._failures == LOST_AFTER:
            self._lost_since = self._first_failure_at
            self._status = None
            self._publish()   # before the event, so its context shows the lost link
            self._emit(Level.ERROR, "ENLACE_PERDIDO",
                       f"{LOST_AFTER} comandos seguidos sin respuesta",
                       f"se consulta el estado cada {STATUS_INTERVAL_S * 1000:.0f} ms "
                       "y se reconecta en cuanto responda")

    def _on_reply(self):
        self._failures = 0
        if self._lost_since is None:
            return
        outage = time.monotonic() - self._lost_since
        self._lost_since = None
        self._publish()
        with self._cv:
            resume = self._wants_control and outage <= RESUME_WITHIN_S
            give_up = self._wants_control and not resume
            if resume:   # ahead of any gesture waiting
                self._queue.appendleft(_Outgoing("ARM", Command.ARM, automatic=True))
                self._cv.notify()
            if give_up:
                self._wants_control = False
        self._emit(Level.INFO, "ENLACE_RESTABLECIDO", f"tras {outage:.1f} s sin respuesta",
                   "se envía ARM para conservar o retomar el control" if resume else "")
        if give_up:
            self._emit(Level.WARNING, "CONTROL_NO_RETOMADO",
                       f"el enlace estuvo caído {outage:.0f} s (más de {RESUME_WITHIN_S:.0f} s)",
                       "por seguridad no se rearma solo; pulsar Armar")

    def _on_nack(self, out: _Outgoing, reply: Reply):
        reason = error_name(reply.frame.payload)
        if out.command is Command.SET_TARGET:
            self.clear_target()   # e.g. NOT_CONTROLLER: stop streaming
        if out.command is Command.ARM:
            with self._cv:
                self._wants_control = False
        if not out.user:
            return
        if out.automatic:
            self._emit(Level.WARNING, "CONTROL_NO_RETOMADO",
                       f"ARM rechazado: {reason}",
                       "otro cliente controla SOFIA; pulsar Armar cuando lo libere")
        else:
            not_controller = reply.frame.payload[:1] == bytes([ErrorCode.NOT_CONTROLLER])
            self._emit(Level.WARNING, "COMANDO_RECHAZADO", f"{out.name}: {reason}",
                       "pulsar Armar para tomar el control" if not_controller
                       else "el comando se descarta")

    def _on_status(self, status: Status):
        self._status = status
        with self._cv:
            arm_waiting = any(o.command is Command.ARM for o in self._queue)
            # The user armed, no ARM is pending (a NACK clears _wants_control)
            # and the link stayed up: someone's STOP took control away.
            # Don't fight it.
            released = self._wants_control and not status.is_controller and not arm_waiting
            if released:
                self._wants_control = False
        if released:
            self._emit(Level.WARNING, "CONTROL_LIBERADO",
                       "otro cliente detuvo SOFIA o tomó el control",
                       "no se rearma solo (STOP tiene prioridad); pulsar Armar")
        self._publish()

    def _finish_urgent(self, out: _Outgoing):
        with self._cv:
            if self._urgent and self._urgent[0] is out:
                self._urgent.popleft()

    def _emit(self, level: Level, name: str, detail: str, action: str):
        self._on_event(Event(level, SOURCE, name, detail, action))

    def _publish(self):
        samples = sorted(self._latencies)
        n = len(samples)
        p95 = samples[min(n - 1, -(-95 * n // 100) - 1)] if n else None
        within = sum(ms <= 500 for ms in samples) / n if n else None
        self._on_info(LinkInfo(
            online=self._failures < LOST_AFTER, status=self._status,
            p95_ms=p95, within_500=within,
        ))
