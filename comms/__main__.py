"""
Command-line client for the SOFIA Maestro.

    python -m comms ping [--host 192.168.4.1] [--port 4210] [--count 4]

The PC must be connected to the Maestro's Wi-Fi network (SOFIA-Maestro).
"""

import argparse
import random
import socket
import sys
import time
from dataclasses import dataclass

from comms.protocol import (
    Command,
    DecodeError,
    ErrorCode,
    Frame,
    FrameType,
    decode,
    encode,
    parse_ping_ack,
)

DEFAULT_HOST = "192.168.4.1"
DEFAULT_PORT = 4210
TIMEOUT_S = 0.2        # wait for an ACK/NACK before retransmitting
MAX_RETRIES = 3        # retransmissions with the same SEQ
PING_INTERVAL_S = 1.0
RECEIVE_BUFFER = 1024


@dataclass(frozen=True)
class Reply:
    frame: Frame
    rtt_ms: float      # measured from the last transmission
    retries: int


def request(sock: socket.socket, address, command: Frame) -> Reply | None:
    """Send a command and wait for its ACK/NACK, retrying with the same SEQ.

    Returns None if no response arrives after MAX_RETRIES retransmissions.
    """
    data = encode(command)
    for attempt in range(MAX_RETRIES + 1):
        sent_at = time.perf_counter()
        sock.sendto(data, address)
        deadline = sent_at + TIMEOUT_S
        while (remaining := deadline - time.perf_counter()) > 0:
            sock.settimeout(remaining)
            try:
                datagram, _ = sock.recvfrom(RECEIVE_BUFFER)
            except TimeoutError:
                break
            try:
                frame = decode(datagram)
            except DecodeError:
                continue   # corrupted: ignore, keep waiting
            if frame.seq == command.seq and frame.command == command.command \
                    and frame.type in (FrameType.ACK, FrameType.NACK):
                return Reply(frame, (time.perf_counter() - sent_at) * 1000, attempt)
            # A late reply to an earlier command: ignore it
    return None


def error_name(payload: bytes) -> str:
    if not payload:
        return "missing error code"
    try:
        return ErrorCode(payload[0]).name
    except ValueError:
        return f"0x{payload[0]:02X}"


def describe(reply: Reply) -> str:
    frame = reply.frame
    if frame.type is FrameType.NACK:
        return f"NACK seq={frame.seq} error={error_name(frame.payload)}"
    info = parse_ping_ack(frame.payload)
    text = (
        f"ACK seq={frame.seq} firmware={info.firmware} "
        f"uptime={info.uptime_ms / 1000:.1f} s rtt={reply.rtt_ms:.1f} ms"
    )
    if reply.retries:
        text += f" (after {reply.retries} retransmission(s))"
    return text


def ping(host: str, port: int, count: int) -> int:
    """PING the Maestro `count` times. Returns the process exit code."""
    address = (host, port)
    # Random first SEQ, so a quick restart isn't mistaken for a retransmission
    seq = random.randrange(0x10000)
    rtts = []
    sent = rejected = 0

    print(f"PING SOFIA Maestro at {host}:{port} (UDP)")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            for i in range(count):
                if i:
                    time.sleep(PING_INTERVAL_S)
                seq = (seq + 1) & 0xFFFF
                sent += 1
                reply = request(sock, address, Frame(FrameType.COMMAND, seq, Command.PING))
                if reply is None:
                    print(f"seq={seq}: no response after {MAX_RETRIES} retransmissions")
                    continue
                print(describe(reply))
                if reply.frame.type is FrameType.ACK:
                    rtts.append(reply.rtt_ms)
                else:
                    rejected += 1
        except KeyboardInterrupt:
            pass
        except OSError as exc:
            print(f"Network error: {exc}. Is the PC connected to SOFIA-Maestro?")

    acked = len(rtts)
    unanswered = sent - acked - rejected
    print(f"--- {sent} sent, {acked} acknowledged, {rejected} rejected (NACK), "
          f"{unanswered} unanswered")
    if rtts:
        print(f"rtt min/avg/max = {min(rtts):.1f}/{sum(rtts) / acked:.1f}/{max(rtts):.1f} ms")
    return 0 if acked else 1


def parse_args():
    parser = argparse.ArgumentParser(description="SOFIA Maestro client")
    commands = parser.add_subparsers(dest="command", required=True)
    ping_parser = commands.add_parser("ping", help="check the link with PING")
    ping_parser.add_argument("--host", default=DEFAULT_HOST)
    ping_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    ping_parser.add_argument("--count", type=int, default=4)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "ping":
        sys.exit(ping(args.host, args.port, args.count))


if __name__ == "__main__":
    main()
