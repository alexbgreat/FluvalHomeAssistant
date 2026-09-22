"""On-device Auto/Pro schedule models for the Fluval Smart BLE integration.

These describe what the light runs by itself in Auto mode (a single
sunrise/sunset ramp) and Pro mode (a 4-10 point daily timeline); see
docs/SCHEDULING.md for the wire layouts they map onto. Brightness here is
a whole percentage (0-100) per channel, matching those commands - not the
tenths-of-a-percent scale CMD_CTRL uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as TimeOfDay
from typing import Any

PRO_MIN_POINTS = 4
PRO_MAX_POINTS = 10

# The "dynamic effect" trailer (week bitmask, 4-byte window, effect ID) that
# may follow an Auto/Pro schedule. It isn't editable here, but one read back
# from the light is carried through unchanged so saving a schedule doesn't
# silently drop an effect configured from the FluvalSmart app.
DYNAMIC_BLOCK_LEN = 6


@dataclass
class AutoSchedule:
    """Auto mode: fade night -> day over sunrise, day -> night over sunset."""

    sunrise_start: TimeOfDay
    sunrise_end: TimeOfDay
    day: list[int]
    sunset_start: TimeOfDay
    sunset_end: TimeOfDay
    night: list[int]
    turnoff_enabled: bool = False
    turnoff: TimeOfDay = TimeOfDay(0, 0)
    dynamic: bytes | None = None


@dataclass
class ProPoint:
    """One point of a Pro schedule: a time of day and per-channel brightness."""

    at: TimeOfDay
    values: list[int]


@dataclass
class ProSchedule:
    """Pro mode: brightness interpolated between 4-10 points across the day."""

    points: list[ProPoint] = field(default_factory=list)
    dynamic: bytes | None = None

    def sorted_points(self) -> list[ProPoint]:
        return sorted(self.points, key=lambda p: (p.at.hour, p.at.minute))


def clamp_percent(value: Any) -> int:
    return max(0, min(100, int(round(float(value)))))


def format_time(value: TimeOfDay) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def parse_time(value: str) -> TimeOfDay:
    """Parse "HH:MM" (or "HH:MM:SS", dropping the seconds)."""
    parts = value.split(":")
    return TimeOfDay(int(parts[0]), int(parts[1]))


def default_auto_schedule(channel_count: int) -> AutoSchedule:
    return AutoSchedule(
        sunrise_start=TimeOfDay(8, 0),
        sunrise_end=TimeOfDay(9, 0),
        day=[100] * channel_count,
        sunset_start=TimeOfDay(19, 0),
        sunset_end=TimeOfDay(20, 0),
        night=[0] * channel_count,
    )


def default_pro_schedule(channel_count: int, point_count: int = PRO_MIN_POINTS) -> ProSchedule:
    """Evenly spread points from 08:00 to 20:00, dark at both ends."""
    start, span = 8 * 60, 12 * 60
    points = []
    for i in range(point_count):
        minutes = start + round(i * span / (point_count - 1))
        level = 0 if i in (0, point_count - 1) else 100
        points.append(ProPoint(TimeOfDay(minutes // 60, minutes % 60), [level] * channel_count))
    return ProSchedule(points)


def _minutes(value: TimeOfDay) -> int:
    return value.hour * 60 + value.minute


def validate_auto(schedule: AutoSchedule) -> str | None:
    """Return an error code if the Auto schedule's windows are out of order."""
    if _minutes(schedule.sunrise_start) >= _minutes(schedule.sunrise_end):
        return "sunrise_order"
    if _minutes(schedule.sunset_start) >= _minutes(schedule.sunset_end):
        return "sunset_order"
    if _minutes(schedule.sunrise_end) > _minutes(schedule.sunset_start):
        return "sunrise_after_sunset"
    return None


def validate_pro(schedule: ProSchedule) -> str | None:
    """Return an error code if two Pro points share a time of day."""
    if len({_minutes(p.at) for p in schedule.points}) != len(schedule.points):
        return "duplicate_times"
    return None


# --- (de)serialization to config entry options -------------------------------


def _dynamic_to_str(dynamic: bytes | None) -> str | None:
    return dynamic.hex() if dynamic is not None else None


def _dynamic_from_str(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return None
    return raw if len(raw) == DYNAMIC_BLOCK_LEN else None


def auto_to_dict(schedule: AutoSchedule) -> dict[str, Any]:
    return {
        "sunrise_start": format_time(schedule.sunrise_start),
        "sunrise_end": format_time(schedule.sunrise_end),
        "day": list(schedule.day),
        "sunset_start": format_time(schedule.sunset_start),
        "sunset_end": format_time(schedule.sunset_end),
        "night": list(schedule.night),
        "turnoff_enabled": schedule.turnoff_enabled,
        "turnoff": format_time(schedule.turnoff),
        "dynamic": _dynamic_to_str(schedule.dynamic),
    }


def auto_from_dict(data: Any, channel_count: int) -> AutoSchedule | None:
    try:
        schedule = AutoSchedule(
            sunrise_start=parse_time(data["sunrise_start"]),
            sunrise_end=parse_time(data["sunrise_end"]),
            day=[clamp_percent(v) for v in data["day"]],
            sunset_start=parse_time(data["sunset_start"]),
            sunset_end=parse_time(data["sunset_end"]),
            night=[clamp_percent(v) for v in data["night"]],
            turnoff_enabled=bool(data.get("turnoff_enabled", False)),
            turnoff=parse_time(data.get("turnoff", "00:00")),
            dynamic=_dynamic_from_str(data.get("dynamic")),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if len(schedule.day) != channel_count or len(schedule.night) != channel_count:
        return None
    return schedule


def pro_to_dict(schedule: ProSchedule) -> dict[str, Any]:
    return {
        "points": [
            {"time": format_time(p.at), "values": list(p.values)} for p in schedule.sorted_points()
        ],
        "dynamic": _dynamic_to_str(schedule.dynamic),
    }


def pro_from_dict(data: Any, channel_count: int) -> ProSchedule | None:
    try:
        points = [
            ProPoint(parse_time(p["time"]), [clamp_percent(v) for v in p["values"]])
            for p in data["points"]
        ]
    except (KeyError, TypeError, ValueError):
        return None
    if not PRO_MIN_POINTS <= len(points) <= PRO_MAX_POINTS:
        return None
    if any(len(p.values) != channel_count for p in points):
        return None
    return ProSchedule(points, _dynamic_from_str(data.get("dynamic")))
