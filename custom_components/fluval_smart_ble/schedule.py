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
# may follow an Auto/Pro schedule: a storm/cloud/moonlight simulation the
# light plays over its schedule during a daily window. Kept as raw bytes on
# the schedules (see DynamicEffect for the decoded form), so one with an
# unrecognized effect ID is still carried through unchanged.
DYNAMIC_BLOCK_LEN = 6

# Effect IDs, from the FluvalSmart app's DeviceUtil.getDynamicRes().
EFFECTS: dict[int, str] = {
    1: "Thunderstorm 1",
    2: "Thunderstorm 2",
    3: "Thunderstorm 3",
    4: "All colors",
    5: "Cloudy 1",
    6: "Cloudy 2",
    7: "Cloudy 3",
    8: "Cloudy 4",
    9: "Moonlight 1",
    10: "Moonlight 2",
    11: "Moonlight 3",
}
# Days in the week bitmask's bit order (bit 0 = Sunday); bit 7 is the
# effect's master on/off switch.
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_EFFECT_ENABLED_BIT = 0x80


@dataclass
class DynamicEffect:
    """A dynamic effect played on chosen days during a daily window."""

    enabled: bool
    effect: int
    days: list[bool]
    start: TimeOfDay
    end: TimeOfDay

    def to_bytes(self) -> bytes:
        week = sum(1 << i for i, on in enumerate(self.days) if on)
        if self.enabled:
            week |= _EFFECT_ENABLED_BIT
        return bytes(
            [week, self.start.hour, self.start.minute, self.end.hour, self.end.minute, self.effect & 0xFF]
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> DynamicEffect | None:
        if len(raw) != DYNAMIC_BLOCK_LEN:
            return None
        week, sh, sm, eh, em, effect = raw
        if sh > 23 or sm > 59 or eh > 23 or em > 59:
            return None
        return cls(
            enabled=bool(week & _EFFECT_ENABLED_BIT),
            effect=effect,
            days=[bool(week & (1 << i)) for i in range(7)],
            start=TimeOfDay(sh, sm),
            end=TimeOfDay(eh, em),
        )


def default_effect() -> DynamicEffect:
    """Off, but pre-set to a gentle evening moonlight if switched on."""
    return DynamicEffect(
        enabled=False, effect=9, days=[True] * 7, start=TimeOfDay(21, 0), end=TimeOfDay(23, 0)
    )


def validate_effect(effect: DynamicEffect) -> str | None:
    """Return an error code if an enabled effect can't be played."""
    if not effect.enabled:
        return None
    if effect.effect not in EFFECTS:
        return "effect_unknown"
    if not any(effect.days):
        return "effect_no_days"
    if effect.start == effect.end:
        return "effect_window"
    return None


def effect_to_dict(effect: DynamicEffect) -> dict[str, Any]:
    return {
        "enabled": effect.enabled,
        "effect": effect.effect,
        "days": list(effect.days),
        "start": format_time(effect.start),
        "end": format_time(effect.end),
    }


def effect_from_dict(data: Any) -> DynamicEffect | None:
    try:
        effect = DynamicEffect(
            enabled=bool(data["enabled"]),
            effect=int(data["effect"]),
            days=[bool(d) for d in data["days"]],
            start=parse_time(data["start"]),
            end=parse_time(data["end"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if len(effect.days) != 7 or not 0 <= effect.effect <= 255:
        return None
    return effect


def decode_effect(dynamic: bytes | None) -> DynamicEffect:
    """The schedule's effect as stored on the light, or the (disabled) default."""
    return (DynamicEffect.from_bytes(dynamic) if dynamic is not None else None) or default_effect()


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


# --- Sun sync ------------------------------------------------------------------

DAY_MINUTES = 24 * 60
# Offsets and ramp lengths are limited so a ramp always stays within a day.
SUN_OFFSET_LIMIT = 6 * 60
SUN_DURATION_MIN = 1
SUN_DURATION_MAX = 4 * 60


@dataclass
class SunSyncConfig:
    """Sun sync: an Auto schedule whose ramps follow the real sunrise/sunset.

    Each ramp starts `offset` minutes after the sun event (negative means
    before it) and lasts `duration` minutes. The defaults finish the sunset
    fade right at sunset, and start the sunrise fade right at sunrise.
    """

    day: list[int]
    night: list[int]
    sunrise_offset: int = 0
    sunrise_duration: int = 60
    sunset_offset: int = -60
    sunset_duration: int = 60
    turnoff_enabled: bool = False
    turnoff: TimeOfDay = TimeOfDay(0, 0)
    # When the next day's schedule is pushed to the light each night.
    push_time: TimeOfDay = TimeOfDay(3, 0)
    # None (settings saved before effects were editable) keeps whatever
    # effect the light's Auto schedule already has.
    effect: DynamicEffect | None = None


def default_sun_sync_config(auto: AutoSchedule) -> SunSyncConfig:
    """Start from the brightness levels of an existing Auto schedule."""
    return SunSyncConfig(
        day=list(auto.day),
        night=list(auto.night),
        turnoff_enabled=auto.turnoff_enabled,
        turnoff=auto.turnoff,
        effect=decode_effect(auto.dynamic),
    )


def _clamp_minutes(value: int) -> int:
    return max(0, min(DAY_MINUTES - 1, value))


def _from_minutes(value: int) -> TimeOfDay:
    return TimeOfDay(value // 60, value % 60)


def build_sun_schedule(
    config: SunSyncConfig, sunrise: TimeOfDay, sunset: TimeOfDay
) -> tuple[AutoSchedule, str | None]:
    """Build the Auto schedule for a day with the given (local) sun times.

    Returns the schedule plus an error code if it can't be used: "outside_day"
    if a ramp would run past midnight (the light's schedule is a single day;
    the returned schedule is clipped to it), otherwise validate_auto()'s code
    if the ramps are out of order (e.g. sunrise ending after sunset starts).
    """
    raw = [
        _minutes(sunrise) + config.sunrise_offset,
        _minutes(sunrise) + config.sunrise_offset + config.sunrise_duration,
        _minutes(sunset) + config.sunset_offset,
        _minutes(sunset) + config.sunset_offset + config.sunset_duration,
    ]
    sr_start, sr_end, ss_start, ss_end = (_clamp_minutes(m) for m in raw)
    schedule = AutoSchedule(
        sunrise_start=_from_minutes(sr_start),
        sunrise_end=_from_minutes(sr_end),
        day=list(config.day),
        sunset_start=_from_minutes(ss_start),
        sunset_end=_from_minutes(ss_end),
        night=list(config.night),
        turnoff_enabled=config.turnoff_enabled,
        turnoff=config.turnoff,
        dynamic=config.effect.to_bytes() if config.effect is not None else None,
    )
    if any(not 0 <= m < DAY_MINUTES for m in raw):
        return schedule, "outside_day"
    return schedule, validate_auto(schedule)


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


def sun_sync_to_dict(config: SunSyncConfig) -> dict[str, Any]:
    return {
        "day": list(config.day),
        "night": list(config.night),
        "sunrise_offset": config.sunrise_offset,
        "sunrise_duration": config.sunrise_duration,
        "sunset_offset": config.sunset_offset,
        "sunset_duration": config.sunset_duration,
        "turnoff_enabled": config.turnoff_enabled,
        "turnoff": format_time(config.turnoff),
        "push_time": format_time(config.push_time),
        "effect": effect_to_dict(config.effect) if config.effect is not None else None,
    }


def sun_sync_from_dict(data: Any, channel_count: int) -> SunSyncConfig | None:
    def offset(value: Any) -> int:
        return max(-SUN_OFFSET_LIMIT, min(SUN_OFFSET_LIMIT, int(value)))

    def duration(value: Any) -> int:
        return max(SUN_DURATION_MIN, min(SUN_DURATION_MAX, int(value)))

    try:
        config = SunSyncConfig(
            day=[clamp_percent(v) for v in data["day"]],
            night=[clamp_percent(v) for v in data["night"]],
            sunrise_offset=offset(data["sunrise_offset"]),
            sunrise_duration=duration(data["sunrise_duration"]),
            sunset_offset=offset(data["sunset_offset"]),
            sunset_duration=duration(data["sunset_duration"]),
            turnoff_enabled=bool(data.get("turnoff_enabled", False)),
            turnoff=parse_time(data.get("turnoff", "00:00")),
            push_time=parse_time(data.get("push_time", "03:00")),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if data.get("effect") is not None:
        if (effect := effect_from_dict(data["effect"])) is None:
            return None
        config.effect = effect
    if len(config.day) != channel_count or len(config.night) != channel_count:
        return None
    return config
