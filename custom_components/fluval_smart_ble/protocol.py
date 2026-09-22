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
from datetime import datetime, time as TimeOfDay

from .const import (
    BRIGHTNESS_SCALE,
    CHANNEL_UNCHANGED,
    CMD_CTRL,
    CMD_CYCLE,
    CMD_DYN,
    CMD_FIND,
    CMD_MODE,
    CMD_PRO,
    CMD_READ,
    CMD_SWITCH,
    CMD_SYNCTIME,
    FRAME_HEADER,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PRO,
)
from .schedule import (
    DYNAMIC_BLOCK_LEN,
    PRO_MAX_POINTS,
    PRO_MIN_POINTS,
    AutoSchedule,
    ProPoint,
    ProSchedule,
    clamp_percent,
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
    """Reassembles a decoded command frame from one or more BLE notifications.

    A response longer than ~17 plaintext bytes doesn't arrive as one
    encode_message() output split across notifications by the BLE
    transport - each notification is its OWN complete, independently
    wrapped chunk (its own random key and [0x54, len, key] header),
    mirroring how the app's BleManager.sendBytes() chunks and
    independently encodes its own outgoing writes longer than 17 bytes.
    So every notification must be decoded on its own, and the *decoded
    plaintexts* concatenated to rebuild the full logical frame - not the
    raw wire bytes concatenated and decoded once.
    """

    def __init__(self) -> None:
        self._raw_buffer = bytearray()
        self._plaintext = bytearray()
        self._last_update = 0.0

    def feed(self, data: bytes) -> bytes | None:
        """Feed newly received notification bytes.

        Returns the decoded, checksum-valid plaintext frame once a
        complete message has been reassembled, otherwise None.
        """
        now = time.monotonic()
        if (self._raw_buffer or self._plaintext) and (now - self._last_update) > REASSEMBLY_TIMEOUT:
            self._raw_buffer.clear()
            self._plaintext.clear()
        self._last_update = now
        self._raw_buffer.extend(data)

        while True:
            total = expected_wire_length(bytes(self._raw_buffer))
            if total is None or len(self._raw_buffer) < total:
                return None

            wire = bytes(self._raw_buffer[:total])
            del self._raw_buffer[:total]
            try:
                chunk = decode_message(wire)
            except ValueError:
                self._plaintext.clear()
                continue
            self._plaintext.extend(chunk)

            frame = bytes(self._plaintext)
            if len(frame) >= 3 and frame[0] == FRAME_HEADER and xor_checksum(frame[:-1]) == frame[-1]:
                self._plaintext.clear()
                return frame
            # Not yet a complete, checksum-valid frame - keep looping in
            # case more whole chunks are already sitting in the raw
            # buffer; otherwise the length check above returns None and
            # we wait for the next notification.


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


def frame_play_effect(effect: int) -> bytes:
    """Build a CMD_DYN frame (the app's sendKey()) for a dynamic effect ID.

    Its exact behavior is unconfirmed (see docs/SCHEDULING.md); the app's
    naming suggests it plays the effect on the light right away.
    """
    return build_frame(CMD_DYN, bytes([effect & 0xFF]))


def frame_sync_time(when: datetime) -> bytes:
    """Build a CMD_SYNCTIME frame setting the light's on-board clock.

    `when` should be local time - the light schedules its Auto/Pro modes
    against its own on-board clock, with no timezone concept of its own.
    Field layout and byte order match the app's syncDeviceTime():
    year-2000, month (0-based), day, weekday (0=Sunday..6=Saturday), hour,
    minute, second.
    """
    return build_frame(
        CMD_SYNCTIME,
        bytes(
            [
                max(0, when.year - 2000) & 0xFF,
                (when.month - 1) & 0xFF,
                when.day & 0xFF,
                (when.isoweekday() % 7) & 0xFF,
                when.hour & 0xFF,
                when.minute & 0xFF,
                when.second & 0xFF,
            ]
        ),
    )


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


def _time_bytes(value: TimeOfDay) -> bytes:
    return bytes([value.hour % 24, value.minute % 60])


def _read_time(data: bytes, offset: int) -> TimeOfDay:
    hour, minute = data[offset], data[offset + 1]
    if hour > 23 or minute > 59:
        raise ValueError(f"invalid time {hour}:{minute}")
    return TimeOfDay(hour, minute)


def frame_set_auto(schedule: AutoSchedule) -> bytes:
    """Build a CMD_CYCLE frame programming the Auto mode schedule.

    The optional blocks after the base schedule are told apart purely by
    total length, so the turn-off block is always sent (disabled via its
    own enable byte when unused) to keep the variant unambiguous. A
    dynamic-effect block read back from the light is re-sent unchanged.
    """
    args = bytearray()
    args += _time_bytes(schedule.sunrise_start) + _time_bytes(schedule.sunrise_end)
    args += bytes(clamp_percent(v) for v in schedule.day)
    args += _time_bytes(schedule.sunset_start) + _time_bytes(schedule.sunset_end)
    args += bytes(clamp_percent(v) for v in schedule.night)
    args += bytes([1 if schedule.turnoff_enabled else 0]) + _time_bytes(schedule.turnoff)
    if schedule.dynamic is not None and len(schedule.dynamic) == DYNAMIC_BLOCK_LEN:
        args += schedule.dynamic
    return build_frame(CMD_CYCLE, bytes(args))


def frame_set_pro(schedule: ProSchedule) -> bytes:
    """Build a CMD_PRO frame programming the Pro mode schedule.

    Points are sorted by time of day first, as the app does before
    sending them.
    """
    points = schedule.sorted_points()
    if not PRO_MIN_POINTS <= len(points) <= PRO_MAX_POINTS:
        raise ValueError(f"a Pro schedule needs {PRO_MIN_POINTS}-{PRO_MAX_POINTS} points")
    args = bytearray([len(points)])
    for point in points:
        args += _time_bytes(point.at)
        args += bytes(clamp_percent(v) for v in point.values)
    if schedule.dynamic is not None and len(schedule.dynamic) == DYNAMIC_BLOCK_LEN:
        args += schedule.dynamic
    return build_frame(CMD_PRO, bytes(args))


def parse_auto_schedule(args: bytes, channel_count: int) -> AutoSchedule | None:
    """Decode an Auto schedule from a CMD_READ response's bytes after the mode byte."""
    n = channel_count
    base = 8 + 2 * n
    extra = len(args) - base
    if extra not in (0, 3, DYNAMIC_BLOCK_LEN, 3 + DYNAMIC_BLOCK_LEN):
        return None
    try:
        schedule = AutoSchedule(
            sunrise_start=_read_time(args, 0),
            sunrise_end=_read_time(args, 2),
            day=[clamp_percent(b) for b in args[4 : 4 + n]],
            sunset_start=_read_time(args, 4 + n),
            sunset_end=_read_time(args, 6 + n),
            night=[clamp_percent(b) for b in args[8 + n : 8 + 2 * n]],
        )
        rest = args[base:]
        if extra in (3, 3 + DYNAMIC_BLOCK_LEN):
            schedule.turnoff_enabled = bool(rest[0])
            schedule.turnoff = _read_time(rest, 1)
            rest = rest[3:]
    except ValueError:
        return None
    if len(rest) == DYNAMIC_BLOCK_LEN:
        schedule.dynamic = bytes(rest)
    return schedule


def parse_pro_schedule(args: bytes, channel_count: int) -> ProSchedule | None:
    """Decode a Pro schedule from a CMD_READ response's bytes after the mode byte."""
    if not args:
        return None
    count = args[0]
    if not PRO_MIN_POINTS <= count <= PRO_MAX_POINTS:
        return None
    stride = channel_count + 2
    base = 1 + count * stride
    if len(args) not in (base, base + DYNAMIC_BLOCK_LEN):
        return None
    points = []
    try:
        for i in range(count):
            offset = 1 + i * stride
            points.append(
                ProPoint(
                    _read_time(args, offset),
                    [clamp_percent(b) for b in args[offset + 2 : offset + stride]],
                )
            )
    except ValueError:
        return None
    dynamic = bytes(args[base:]) if len(args) > base else None
    return ProSchedule(points, dynamic)


@dataclass
class ParsedState:
    """Result of parsing a CMD_READ response."""

    mode: int
    is_on: bool | None
    channel_values: list[float] | None
    auto_schedule: AutoSchedule | None = None
    pro_schedule: ProSchedule | None = None


def parse_read_response(frame: bytes, channel_count: int) -> ParsedState | None:
    """Parse a CMD_READ response frame.

    Only the manual-mode response carries live on/off and per-channel
    brightness; auto/pro-mode responses instead carry the stored schedule
    for that mode, which is decoded on a best-effort basis (None if its
    length doesn't match any known layout).
    """
    if len(frame) < 4:
        return None
    if frame[0] != FRAME_HEADER or frame[1] != CMD_READ:
        return None
    if xor_checksum(frame[:-1]) != frame[-1]:
        return None

    mode = frame[2]
    if mode == MODE_AUTO:
        return ParsedState(
            mode=mode,
            is_on=None,
            channel_values=None,
            auto_schedule=parse_auto_schedule(frame[3:-1], channel_count),
        )
    if mode == MODE_PRO:
        return ParsedState(
            mode=mode,
            is_on=None,
            channel_values=None,
            pro_schedule=parse_pro_schedule(frame[3:-1], channel_count),
        )
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
