"""
Command-line client for the SOFIA Maestro.

    python -m comms ping [--count 4]
    python -m comms status
    python -m comms arm
    python -m comms gesture {INDEX,V,PALM,SWIPE_LEFT,SWIPE_RIGHT,GRAB}
    python -m comms target X Y [--seconds 1]
    python -m comms angles Q1 Q2 [--module N] [--seconds 1]
    python -m comms stop
    python -m comms off

Every command accepts --host (default 192.168.4.1) and --port (4210).
The PC must be connected to the Maestro's Wi-Fi network (SOFIA-Maestro).

The Maestro identifies a UDP client by its IP and port. Every run uses
local port 4211 (--local-port), so consecutive runs are the same client.
`gesture`, `target` and `angles` send ARM first: it takes control only if
no other client holds it (control lapses 3 s after a client's last
command) and doesn't re-centre servos that are already powered.
"""

import argparse
import sys
import time

from comms.link import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_RETRIES,
    PING_INTERVAL_S,
    Reply,
    Session,
    error_name,
)
from comms.protocol import (
    MODULES,
    Command,
    FrameType,
    Gesture,
    angles_payload,
    gesture_payload,
    parse_angles,
    parse_ping_ack,
    parse_status_ack,
    target_payload,
)

STREAM_HZ = 25
LOCAL_PORT = 4211


def describe(reply: Reply) -> str:
    frame = reply.frame
    if frame.type is FrameType.NACK:
        return f"NACK seq={frame.seq} error={error_name(frame.payload)}"
    text = f"ACK seq={frame.seq}"
    if frame.command == Command.PING:
        info = parse_ping_ack(frame.payload)
        text += f" firmware={info.firmware} uptime={info.uptime_ms / 1000:.1f} s"
    text += f" rtt={reply.rtt_ms:.1f} ms"
    if reply.retries:
        text += f" (after {reply.retries} retransmission(s))"
    return text


def ping(session: Session, count: int) -> int:
    """PING the Maestro `count` times. Returns the process exit code."""
    rtts = []
    sent = rejected = 0
    host, port = session.address
    print(f"PING SOFIA Maestro at {host}:{port} (UDP)")
    try:
        for i in range(count):
            if i:
                time.sleep(PING_INTERVAL_S)
            sent += 1
            reply = session.send(Command.PING)
            if reply is None:
                print(f"no response after {MAX_RETRIES} retransmissions")
                continue
            print(describe(reply))
            if reply.ok:
                rtts.append(reply.rtt_ms)
            else:
                rejected += 1
    except KeyboardInterrupt:
        pass

    acked = len(rtts)
    unanswered = sent - acked - rejected
    print(f"--- {sent} sent, {acked} acknowledged, {rejected} rejected (NACK), "
          f"{unanswered} unanswered")
    if rtts:
        print(f"rtt min/avg/max = {min(rtts):.1f}/{sum(rtts) / acked:.1f}/{max(rtts):.1f} ms")
    return 0 if acked else 1


def run(session: Session, name: str, command: Command, payload: bytes = b"") -> bool:
    """Send one command and print the outcome. True if acknowledged."""
    reply = session.send(command, payload)
    if reply is None:
        print(f"{name}: no response after {MAX_RETRIES} retransmissions")
        return False
    print(f"{name}: {describe(reply)}")
    return reply.ok


def status(session: Session) -> int:
    reply = session.send(Command.GET_STATUS)
    if reply is None or not reply.ok:
        print(describe(reply) if reply else "no response")
        return 1
    s = parse_status_ack(reply.frame.payload)
    boards = "simulated" if s.simulated else ("ready" if s.healthy else "NOT answering")
    print(f"PWM boards:  {boards}")
    print(f"Mode:        {s.mode.name}" + (f" ({s.gesture.name})" if s.gesture else ""))
    print(f"Armed:       {'yes' if s.armed else 'no'} ({s.powered}/32 servos powered)")
    controller = "nobody" if s.controller.name == "NONE" else s.controller.name
    print(f"Controller:  {controller}" + (" (this client)" if s.is_controller else ""))
    print(f"Limit:       ±{s.limit_deg}°")
    print(f"Counters:    {s.watchdog_holds} watchdog holds, {s.nacks} NACKs, "
          f"{s.discarded} discarded frames")

    reply = session.send(Command.GET_ANGLES)
    if reply is not None and reply.ok:
        angles = parse_angles(reply.frame.payload)
        print("Angles (q1, q2) in degrees:")
        for i in range(0, MODULES, 4):
            print("  " + "  ".join(f"{j:2d}: ({q1:6.2f}, {q2:6.2f})"
                                   for j, (q1, q2) in enumerate(angles[i:i + 4], start=i)))
    return 0


def stream(session: Session, name: str, command: Command, payload: bytes,
           seconds: float) -> int:
    """Send the same stream command at STREAM_HZ for `seconds`."""
    if not run(session, "ARM", Command.ARM):
        return 1
    sent = acked = 0
    rtts = []
    end = time.perf_counter() + seconds
    try:
        while True:
            # A stale value isn't worth retransmitting (PROTOCOL.md §8.2)
            reply = session.send(command, payload, max_retries=0)
            sent += 1
            if reply is not None:
                if not reply.ok:
                    print(f"{name}: {describe(reply)}")
                    return 1
                acked += 1
                rtts.append(reply.rtt_ms)
            if time.perf_counter() >= end:
                break
            time.sleep(1 / STREAM_HZ)
    except KeyboardInterrupt:
        pass
    rtts.sort()
    p95 = rtts[min(len(rtts) - 1, int(0.95 * len(rtts)))] if rtts else float("nan")
    print(f"{name}: {acked}/{sent} acknowledged, rtt p95 = {p95:.1f} ms. "
          "The Maestro holds the pose 0.5 s after the last one.")
    return 0 if acked else 1


def parse_args():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=DEFAULT_HOST)
    common.add_argument("--port", type=int, default=DEFAULT_PORT)
    common.add_argument("--local-port", type=int, default=LOCAL_PORT,
                        help="UDP port on this PC (0 = any)")

    parser = argparse.ArgumentParser(description="SOFIA Maestro client")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("ping", parents=[common], help="check the link with PING")
    p.add_argument("--count", type=int, default=4)
    commands.add_parser("status", parents=[common], help="show the state and the joint angles")
    commands.add_parser("arm", parents=[common], help="take control and power the servos")
    commands.add_parser("stop", parents=[common], help="hold the pose and release control")
    commands.add_parser("off", parents=[common], help="turn every PWM output off")

    p = commands.add_parser("gesture", parents=[common], help="play a gesture's motion")
    p.add_argument("name", choices=[g.name for g in Gesture], type=str.upper)

    p = commands.add_parser("target", parents=[common],
                            help="point at a camera point (0-1, mirrored, y down)")
    p.add_argument("x", type=float)
    p.add_argument("y", type=float)
    p.add_argument("--seconds", type=float, default=1.0)

    p = commands.add_parser("angles", parents=[common],
                            help="set joint angles in degrees (calibration)")
    p.add_argument("q1", type=float)
    p.add_argument("q2", type=float)
    p.add_argument("--module", type=int, choices=range(MODULES), metavar="N",
                   help="only this module (0-15); the others go to 0°")
    p.add_argument("--seconds", type=float, default=1.0)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        with Session(args.host, args.port, args.local_port) as session:
            if args.command == "ping":
                code = ping(session, args.count)
            elif args.command == "status":
                code = status(session)
            elif args.command == "arm":
                code = 0 if run(session, "ARM", Command.ARM) else 1
            elif args.command == "stop":
                code = 0 if run(session, "STOP", Command.STOP) else 1
            elif args.command == "off":
                code = 0 if run(session, "OFF", Command.OFF) else 1
            elif args.command == "gesture":
                gesture = Gesture[args.name]
                code = 0 if (run(session, "ARM", Command.ARM) and run(
                    session, f"GESTURE {gesture.name}", Command.GESTURE,
                    gesture_payload(gesture))) else 1
            elif args.command == "target":
                if not (0 <= args.x <= 1 and 0 <= args.y <= 1):
                    sys.exit("x and y must be between 0 and 1")
                code = stream(session, "SET_TARGET", Command.SET_TARGET,
                              target_payload(args.x, args.y), args.seconds)
            else:
                pose = [(0.0, 0.0)] * MODULES
                if args.module is None:
                    pose = [(args.q1, args.q2)] * MODULES
                else:
                    pose[args.module] = (args.q1, args.q2)
                try:
                    payload = angles_payload(pose)
                except ValueError as exc:
                    sys.exit(str(exc))
                code = stream(session, "SET_ANGLES", Command.SET_ANGLES, payload, args.seconds)
    except OSError as exc:
        print(f"Network error: {exc}. Is the PC connected to SOFIA-Maestro?")
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
