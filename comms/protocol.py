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
    PING = 0x01
    # Reserved for later stages
    GET_STATUS = 0x02
    GESTURE = 0x10
    SET_TARGET = 0x11


class ErrorCode(IntEnum):
    UNKNOWN_COMMAND = 0x01
    BAD_LENGTH = 0x02
    BAD_PAYLOAD = 0x03
    UNSUPPORTED_VERSION = 0x04
    BUSY = 0x05
    INTERNAL = 0x06


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
