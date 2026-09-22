"""Fluval Smart BLE wire protocol.

The FluvalSmart Android app wraps every command/response frame in a
trivial byte-oriented obfuscation before writing it to (or reading it
from) the BLE characteristic. It is implemented in a bundled native
library (`libhy_api.so`, functions `encodeMessage`/`decodeMessage`) which
was reverse engineered for this integration:

    wire[0]   = 0x54
    wire[1]   = (len(payload) + 1) ^ 0x54
    wire[2]   = key ^ 0x54                   (key is any random byte)
    wire[3:]  = [b ^ key for b in payload]

There is no real cryptography involved - the "key" travels in the
message itself, obfuscated by a fixed XOR with 0x54.

The unwrapped payload is a small frame:

    payload[0]  = 0x68             (frame header)
    payload[1]  = command byte
    payload[2:-1] = command arguments
    payload[-1] = XOR checksum of everything before it

This module implements that framing plus helpers to build the specific
commands used to control a light, and to parse its state responses.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .const import (
    BRIGHTNESS_SCALE,
    CHANNEL_UNCHANGED,
    CMD_CTRL,
    CMD_FIND,
    CMD_MODE,
    CMD_READ,
    CMD_SWITCH,
    FRAME_HEADER,
    MODE_MANUAL,
)

_OBFUSCATION_KEY = 0x54
# The wire format's inner buffer is reused between calls, so a fresh
# reassembly is abandoned if no new notification bytes arrive within this
# window (matches the Android app's own 64ms cutoff).
REASSEMBLY_TIMEOUT = 0.064


def xor_checksum(data: bytes) -> int:
    """XOR every byte together (the frame's checksum algorithm)."""
    checksum = 0
    for b in data:
        checksum ^= b
    return checksum


def encode_message(payload: bytes) -> bytes:
    """Wrap a plaintext frame for transmission over BLE."""
    key = random.randint(0, 255)
    length_byte = ((len(payload) + 1) ^ _OBFUSCATION_KEY) & 0xFF
    key_byte = (key ^ _OBFUSCATION_KEY) & 0xFF
    body = bytes(b ^ key for b in payload)
    return bytes([_OBFUSCATION_KEY, length_byte, key_byte]) + body


def decode_message(wire: bytes) -> bytes:
    """Unwrap a frame received over BLE."""
    if len(wire) < 3:
        raise ValueError("wire message too short")
    key = wire[2] ^ wire[0]
    return bytes(b ^ key for b in wire[3:])


def expected_wire_length(wire_prefix: bytes) -> int | None:
    """Return the total wire length once enough bytes have arrived to know it."""
    if len(wire_prefix) < 2:
        return None
    payload_len = (wire_prefix[1] ^ _OBFUSCATION_KEY) - 1
    if payload_len < 0:
        return None
    return payload_len + 3


class FrameReassembler:
    """Reassembles a decoded command frame from one or more BLE notifications."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._last_update = 0.0

    def feed(self, data: bytes) -> bytes | None:
        """Feed newly received notification bytes.

        Returns the decoded plaintext frame once a complete message has
        been received, otherwise None.
        """
        now = time.monotonic()
        if self._buffer and (now - self._last_update) > REASSEMBLY_TIMEOUT:
            self._buffer.clear()
        self._last_update = now
        self._buffer.extend(data)

        total = expected_wire_length(bytes(self._buffer))
        if total is None or len(self._buffer) < total:
            return None

        wire = bytes(self._buffer[:total])
        del self._buffer[:total]
        try:
            return decode_message(wire)
        except ValueError:
            return None


def build_frame(cmd: int, args: bytes = b"") -> bytes:
    """Build a plaintext command frame, appending its checksum."""
    body = bytes([FRAME_HEADER, cmd]) + args
    return body + bytes([xor_checksum(body)])


def frame_turn_on() -> bytes:
    return build_frame(CMD_SWITCH, bytes([1]))


def frame_turn_off() -> bytes:
    return build_frame(CMD_SWITCH, bytes([0]))


def frame_set_mode(mode: int) -> bytes:
    return build_frame(CMD_MODE, bytes([mode]))


def frame_read() -> bytes:
    return build_frame(CMD_READ)


def frame_find() -> bytes:
    return build_frame(CMD_FIND)


def frame_set_channels(values: list[float | None]) -> bytes:
    """Build a CMD_CTRL frame.

    `values` holds one entry per channel, each a brightness percentage
    (0-100, fractional allowed down to 0.1%) or None to leave that
    channel's current brightness unchanged.
    """
    args = bytearray()
    for value in values:
        if value is None:
            packed = CHANNEL_UNCHANGED
        else:
            tenths = round(max(0.0, min(100.0, value)) * BRIGHTNESS_SCALE)
            packed = min(1000, tenths) & 0xFFFF
        args.append((packed >> 8) & 0xFF)
        args.append(packed & 0xFF)
    return build_frame(CMD_CTRL, bytes(args))


@dataclass
class ParsedState:
    """Result of parsing a CMD_READ response."""

    mode: int
    is_on: bool | None
    channel_values: list[float] | None


def parse_read_response(frame: bytes, channel_count: int) -> ParsedState | None:
    """Parse a CMD_READ response frame.

    Only the manual-mode response carries live on/off and per-channel
    brightness; auto/pro-mode responses only reveal the active mode.
    """
    if len(frame) < 4:
        return None
    if frame[0] != FRAME_HEADER or frame[1] != CMD_READ:
        return None
    if xor_checksum(frame[:-1]) != frame[-1]:
        return None

    mode = frame[2]
    if mode != MODE_MANUAL:
        return ParsedState(mode=mode, is_on=None, channel_values=None)

    expected_len = channel_count * 6 + 6
    if len(frame) != expected_len:
        return None

    is_on = bool(frame[3] & 1)
    values: list[float] = []
    for i in range(channel_count):
        lo = frame[5 + 2 * i]
        hi = frame[6 + 2 * i]
        raw = lo | (hi << 8)
        values.append(raw / BRIGHTNESS_SCALE)
    return ParsedState(mode=mode, is_on=is_on, channel_values=values)
