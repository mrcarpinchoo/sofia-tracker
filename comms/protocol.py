"""
SOFIA PC <-> Maestro protocol, version 1.

The specification is sofia-firmware/docs/PROTOCOL.md; this module mirrors
sofia-firmware/src/protocol.h. Frame layout (little-endian):

    SOF | VER | TYPE | SEQ (2) | CMD | LEN | PAYLOAD (LEN) | CRC16 (2)

The CRC is CRC-16/CCITT-FALSE over VER .. end of PAYLOAD.
"""

import binascii
import struct
from dataclasses import dataclass
from enum import IntEnum

START_OF_FRAME = 0xA5
VERSION = 0x01
MAX_PAYLOAD = 64

_HEADER = struct.Struct("<BBBHBB")   # SOF, VER, TYPE, SEQ, CMD, LEN
_CRC = struct.Struct("<H")
MIN_FRAME_SIZE = _HEADER.size + _CRC.size
MAX_FRAME_SIZE = MIN_FRAME_SIZE + MAX_PAYLOAD


class FrameType(IntEnum):
    COMMAND = 0x01   # PC -> Maestro
    ACK = 0x02       # Maestro -> PC: command accepted and executed
    NACK = 0x03      # Maestro -> PC: command rejected, payload = ErrorCode


class Command(IntEnum):
    # Link and diagnostics
    PING = 0x01
    GET_STATUS = 0x02
    GET_ANGLES = 0x03
    # SOFIA motion
    GESTURE = 0x10
    SET_TARGET = 0x11
    ARM = 0x12
    STOP = 0x13
    OFF = 0x14
    SET_ANGLES = 0x15


class ErrorCode(IntEnum):
    UNKNOWN_COMMAND = 0x01
    BAD_LENGTH = 0x02
    BAD_PAYLOAD = 0x03
    UNSUPPORTED_VERSION = 0x04
    BUSY = 0x05
    INTERNAL = 0x06
    NOT_CONTROLLER = 0x07


class Gesture(IntEnum):
    """GESTURE IDs, named as gestures.py reports them."""
    INDEX = 1
    V = 2
    PALM = 3
    SWIPE_LEFT = 4
    SWIPE_RIGHT = 5
    GRAB = 6


class Mode(IntEnum):
    OFF = 0
    HOLD = 1
    TARGET = 2
    GESTURE = 3
    MANUAL = 4


class Transport(IntEnum):
    NONE = 0
    UDP = 1
    WEBSOCKET = 2


MODULES = 16
LIMIT_DEG = 30.0


class DecodeError(ValueError):
    """The datagram isn't a valid frame and must be discarded."""


@dataclass(frozen=True)
class Frame:
    type: FrameType
    seq: int
    command: int
    payload: bytes = b""
    version: int = VERSION


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, initial value 0xFFFF)."""
    return binascii.crc_hqx(data, 0xFFFF)


def encode(frame: Frame) -> bytes:
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError(f"payload longer than {MAX_PAYLOAD} bytes")
    body = _HEADER.pack(
        START_OF_FRAME, frame.version, frame.type, frame.seq, frame.command,
        len(frame.payload),
    ) + frame.payload
    return body + _CRC.pack(crc16(body[1:]))


def decode(data: bytes) -> Frame:
    if len(data) < MIN_FRAME_SIZE:
        raise DecodeError("too short")
    sof, version, frame_type, seq, command, length = _HEADER.unpack_from(data)
    if sof != START_OF_FRAME:
        raise DecodeError("bad start-of-frame")
    if length > MAX_PAYLOAD or len(data) != MIN_FRAME_SIZE + length:
        raise DecodeError("length mismatch")
    (crc,) = _CRC.unpack_from(data, len(data) - _CRC.size)
    if crc != crc16(data[1:-_CRC.size]):
        raise DecodeError("bad CRC")
    try:
        frame_type = FrameType(frame_type)
    except ValueError:
        raise DecodeError(f"unknown frame type 0x{frame_type:02X}") from None
    payload = data[_HEADER.size:_HEADER.size + length]
    return Frame(frame_type, seq, command, payload, version)


# PING ------------------------------------------------------------------------
_PING_ACK = struct.Struct("<BBBI")   # firmware major, minor, patch; uptime ms


@dataclass(frozen=True)
class PingInfo:
    firmware: str
    uptime_ms: int


def parse_ping_ack(payload: bytes) -> PingInfo:
    if len(payload) != _PING_ACK.size:
        raise DecodeError(f"PING ACK payload must be {_PING_ACK.size} bytes")
    major, minor, patch, uptime_ms = _PING_ACK.unpack(payload)
    return PingInfo(f"{major}.{minor}.{patch}", uptime_ms)


# GET_STATUS ------------------------------------------------------------------
_STATUS_ACK = struct.Struct("<BBBBBBBHHH")


@dataclass(frozen=True)
class Status:
    healthy: bool          # PWM boards answer
    armed: bool
    simulated: bool        # firmware built without servos
    mode: Mode
    gesture: Gesture | None
    controller: Transport  # transport of the client in control
    is_controller: bool    # the client asking is in control
    limit_deg: int
    powered: int           # servos powered since ARM (0-32)
    watchdog_holds: int
    nacks: int
    discarded: int


def parse_status_ack(payload: bytes) -> Status:
    if len(payload) != _STATUS_ACK.size:
        raise DecodeError(f"GET_STATUS ACK payload must be {_STATUS_ACK.size} bytes")
    (flags, mode, gesture, controller, you, limit, powered,
     holds, nacks, discarded) = _STATUS_ACK.unpack(payload)
    return Status(
        healthy=bool(flags & 1), armed=bool(flags & 2), simulated=bool(flags & 4),
        mode=Mode(mode), gesture=Gesture(gesture) if gesture else None,
        controller=Transport(controller), is_controller=bool(you),
        limit_deg=limit, powered=powered,
        watchdog_holds=holds, nacks=nacks, discarded=discarded,
    )


# GESTURE / SET_TARGET ----------------------------------------------------------
def gesture_payload(gesture: Gesture) -> bytes:
    return bytes([Gesture(gesture)])


def target_payload(x: float, y: float) -> bytes:
    """Camera point, 0-1 (image mirrored, y down), as two uint16 0-10000."""
    def scaled(v: float) -> int:
        return round(min(max(v, 0.0), 1.0) * 10000)
    return struct.pack("<HH", scaled(x), scaled(y))


# GET_ANGLES / SET_ANGLES -------------------------------------------------------
# 16 modules x (q1, q2), int16 hundredths of a degree.
_ANGLES = struct.Struct(f"<{2 * MODULES}h")


def angles_payload(angles) -> bytes:
    """`angles`: 16 (q1, q2) pairs in degrees, each within ±LIMIT_DEG."""
    values = []
    for q1, q2 in angles:
        for q in (q1, q2):
            if not -LIMIT_DEG <= q <= LIMIT_DEG:
                raise ValueError(f"angle {q}° outside ±{LIMIT_DEG:g}°")
            values.append(round(q * 100))
    if len(values) != 2 * MODULES:
        raise ValueError(f"need {MODULES} (q1, q2) pairs")
    return _ANGLES.pack(*values)


def parse_angles(payload: bytes) -> list[tuple[float, float]]:
    if len(payload) != _ANGLES.size:
        raise DecodeError(f"angles payload must be {_ANGLES.size} bytes")
    values = _ANGLES.unpack(payload)
    return [(values[2 * i] / 100, values[2 * i + 1] / 100) for i in range(MODULES)]
