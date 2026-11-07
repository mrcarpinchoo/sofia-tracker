"""
UDP exchange with the SOFIA Maestro: send one command and wait for its
ACK/NACK, retransmitting with the same SEQ (sofia-firmware/docs/PROTOCOL.md §8).
"""

import random
import socket
import time
from dataclasses import dataclass

from comms.protocol import Command, DecodeError, ErrorCode, Frame, FrameType, decode, encode

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

    @property
    def ok(self) -> bool:
        return self.frame.type is FrameType.ACK


def request(sock: socket.socket, address, command: Frame,
            max_retries: int = MAX_RETRIES) -> Reply | None:
    """Send a command and wait for its ACK/NACK, retrying with the same SEQ.

    Returns None if no response arrives after `max_retries` retransmissions.
    """
    data = encode(command)
    for attempt in range(max_retries + 1):
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


class Session:
    """One UDP socket and its SEQ counter. Not thread-safe."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 local_port: int = 0):
        """`local_port` 0 lets the OS choose. The Maestro identifies a UDP
        client by IP and port, so a fixed port keeps short-lived clients
        (such as the command-line tool) the same client between runs."""
        self.address = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", local_port))
        # Random first SEQ, so a quick restart isn't mistaken for a retransmission
        self._seq = random.randrange(0x10000)

    def send(self, command: Command, payload: bytes = b"",
             max_retries: int = MAX_RETRIES) -> Reply | None:
        self._seq = (self._seq + 1) & 0xFFFF
        frame = Frame(FrameType.COMMAND, self._seq, command, payload)
        return request(self.sock, self.address, frame, max_retries)

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
