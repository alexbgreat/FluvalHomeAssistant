"""Sun sync mode: keep the light's Auto schedule following the real sun.

The light has no idea where it is or what day of the year it is, so its
Auto mode ramps at fixed times. Sun sync is a Home Assistant-side mode on
top of it: every night Home Assistant computes the coming day's sunrise
and sunset for its configured location, applies the user's offsets, and
pushes the result to the light as its Auto schedule.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from bleak.exc import BleakError

import homeassistant.util.dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.sun import get_astral_event_date

from .const import MODE_SUN_SYNC, MODE_WEATHER_SYNC, MODES_REVERSE
from .coordinator import FluvalCoordinator
from .schedule import (
    AutoSchedule,
    SunSyncConfig,
    build_sun_schedule,
    default_auto_schedule,
    default_effect,
    default_sun_sync_config,
    is_weather_effect,
    sun_sync_from_dict,
    sun_sync_to_dict,
)

_LOGGER = logging.getLogger(__name__)

OPT_SUN_SYNC = "sun_sync"

# A failed nightly push (light out of range, say) is retried this often,
# this many times, before giving up until the next night.
RETRY_DELAY = timedelta(minutes=10)
MAX_RETRIES = 18


class SunSyncError(Exception):
    """Raised when a sun-synced schedule can't be computed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SunSync:
    """Owns one light's sun sync settings, nightly timer and push status."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: FluvalCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        stored = entry.options.get(OPT_SUN_SYNC) or {}
        self.config: SunSyncConfig | None = sun_sync_from_dict(
            stored.get("config"), len(coordinator.model.channels)
        )
        self.enabled: bool = bool(stored.get("enabled")) and self.config is not None
        self.last_push: datetime | None = None
        self.last_schedule: AutoSchedule | None = None
        self.last_error: str | None = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_retry: CALLBACK_TYPE | None = None
        self._retries = 0

    # --- lifecycle ------------------------------------------------------------

    async def async_start(self) -> None:
        """Arm the nightly timer and catch up on any push missed while offline."""
        if not self.enabled:
            return
        self._arm_timer()
        self.hass.async_create_background_task(
            self._async_push_with_retry(), name=f"fluval_smart_ble-sun-sync-{self.coordinator.address}"
        )

    @callback
    def async_stop(self) -> None:
        for unsub in (self._unsub_timer, self._unsub_retry):
            if unsub is not None:
                unsub()
        self._unsub_timer = self._unsub_retry = None

    @callback
    def _arm_timer(self) -> None:
        self.async_stop()
        if not self.enabled or self.config is None:
            return
        at = self.config.push_time
        self._unsub_timer = async_track_time_change(
            self.hass, self._handle_nightly, hour=at.hour, minute=at.minute, second=0
        )

    @callback
    def _handle_nightly(self, _now: datetime) -> None:
        self._retries = 0
        self.hass.async_create_task(self._async_push_with_retry())

    @callback
    def _handle_retry(self, _now: datetime) -> None:
        self._unsub_retry = None
        self.hass.async_create_task(self._async_push_with_retry())

    # --- settings ---------------------------------------------------------------

    @callback
    def _save(self) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                OPT_SUN_SYNC: {
                    "enabled": self.enabled,
                    "config": sun_sync_to_dict(self.config) if self.config is not None else None,
                },
            },
        )
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_enable(self, config: SunSyncConfig | None = None) -> AutoSchedule:
        """Turn sun sync on (optionally with new settings) and push right away.

        The settings are saved and the nightly timer armed even if this
        first push fails, so it's retried in the background as usual.
        """
        if config is None and self.config is None:
            count = len(self.coordinator.model.channels)
            config = default_sun_sync_config(
                self.coordinator.data.auto_schedule or default_auto_schedule(count)
            )
        # Validate before saving anything, so bad offsets are reported
        # rather than silently enabled.
        self.compute(config or self.config)
        if config is not None:
            self.config = config
        self.enabled = True
        self._retries = 0
        self._save()
        self._arm_timer()
        return await self.async_push(activate=True)

    @callback
    def async_disable(self) -> None:
        # Weather sync rides on sun sync, so it goes too.
        if (weather := self.coordinator.weather_sync) is not None:
            weather.async_disable()
        if not self.enabled:
            return
        self.enabled = False
        self.async_stop()
        self._save()

    # --- computing and pushing --------------------------------------------------

    def target_date(self, now: datetime | None = None) -> date:
        """The day a push made now is for.

        A push before noon (the usual nightly push) is for that same day; one
        after noon is for the next day, since today's sunrise has passed.
        """
        now = now or dt_util.now()
        return now.date() if now.hour < 12 else now.date() + timedelta(days=1)

    def sun_times(self, day: date) -> tuple[datetime, datetime] | None:
        """Local sunrise and sunset on `day`, or None if the sun doesn't rise/set."""
        sunrise = get_astral_event_date(self.hass, "sunrise", day)
        sunset = get_astral_event_date(self.hass, "sunset", day)
        if sunrise is None or sunset is None:
            return None
        return dt_util.as_local(sunrise), dt_util.as_local(sunset)

    def compute(self, config: SunSyncConfig | None = None, day: date | None = None) -> AutoSchedule:
        config = config or self.config
        if config is None:
            raise SunSyncError("not_configured", "Sun sync hasn't been set up yet.")
        day = day or self.target_date()
        times = self.sun_times(day)
        if times is None:
            raise SunSyncError("no_sun", f"The sun doesn't both rise and set on {day} at your location.")
        sunrise, sunset = times
        schedule, error = build_sun_schedule(config, sunrise.time(), sunset.time())
        if error == "outside_day":
            raise SunSyncError(
                error,
                f"With these offsets a fade would run past midnight on {day} (sunrise "
                f"{sunrise:%H:%M}, sunset {sunset:%H:%M}); the light's schedule can't cross midnight.",
            )
        if error is not None:
            raise SunSyncError(
                error,
                f"With these offsets the sunrise fade ({schedule.sunrise_start:%H:%M}-"
                f"{schedule.sunrise_end:%H:%M}) and sunset fade ({schedule.sunset_start:%H:%M}-"
                f"{schedule.sunset_end:%H:%M}) overlap on {day}.",
            )
        return schedule

    async def async_push(self, activate: bool = False) -> AutoSchedule:
        """Compute today's/tomorrow's schedule and write it to the light."""
        try:
            schedule = self.compute()
            weather = self.coordinator.weather_sync
            if weather is not None and weather.active:
                # Weather sync picks the effect instead of the settings.
                schedule.dynamic = weather.dynamic_for(schedule)
            elif schedule.dynamic is None:
                schedule.dynamic = self.fallback_dynamic(self.coordinator.data.auto_schedule)
            await self.coordinator.async_set_auto_schedule(schedule, activate)
        except SunSyncError as err:
            self.last_error = str(err)
            self.coordinator.async_set_updated_data(self.coordinator.data)
            raise
        except (BleakError, TimeoutError) as err:
            self.last_error = f"Could not reach the light: {err}"
            self.coordinator.async_set_updated_data(self.coordinator.data)
            raise
        self.last_push = dt_util.now()
        self.last_schedule = schedule
        self.last_error = None
        if weather is not None and weather.active:
            weather.async_pushed(schedule)
        _LOGGER.debug(
            "Sun sync pushed to %s: sunrise %s-%s, sunset %s-%s",
            self.coordinator.address,
            schedule.sunrise_start,
            schedule.sunrise_end,
            schedule.sunset_start,
            schedule.sunset_end,
        )
        return schedule

    def fallback_dynamic(self, current: AutoSchedule | None) -> bytes | None:
        """The effect block for the Auto schedule when weather sync isn't picking one.

        That's the effect in the settings; without one, whatever effect the
        light's Auto schedule already has - unless weather sync left it there,
        which is switched off rather than left playing.
        """
        if self.config is not None and self.config.effect is not None:
            return self.config.effect.to_bytes()
        if current is None:
            return None
        if is_weather_effect(current):
            return default_effect().to_bytes()
        return current.dynamic

    async def _async_push_with_retry(self) -> None:
        if not self.enabled:
            return
        try:
            await self.async_push()
        except SunSyncError as err:
            _LOGGER.warning("Sun sync for %s skipped: %s", self.coordinator.address, err)
        except (BleakError, TimeoutError) as err:
            if self._retries >= MAX_RETRIES:
                _LOGGER.warning(
                    "Sun sync for %s failed, giving up until tomorrow: %s", self.coordinator.address, err
                )
                return
            self._retries += 1
            _LOGGER.debug("Sun sync push to %s failed (%s), retrying", self.coordinator.address, err)
            self._unsub_retry = async_call_later(self.hass, RETRY_DELAY, self._handle_retry)

    def next_push(self) -> datetime | None:
        if not self.enabled or self.config is None:
            return None
        now = dt_util.now()
        at = now.replace(
            hour=self.config.push_time.hour, minute=self.config.push_time.minute, second=0, microsecond=0
        )
        return at if at > now else at + timedelta(days=1)

    def describe(self, fallback: AutoSchedule) -> dict[str, Any]:
        """State for the panel; `fallback` seeds the settings if never configured."""
        config = self.config or default_sun_sync_config(fallback)
        day = self.target_date()
        times = self.sun_times(day)
        return {
            "enabled": self.enabled,
            "config": sun_sync_to_dict(config),
            "date": day.isoformat(),
            "sunrise": f"{times[0]:%H:%M}" if times else None,
            "sunset": f"{times[1]:%H:%M}" if times else None,
            "last_push": self.last_push.isoformat() if self.last_push else None,
            "next_push": (n.isoformat() if (n := self.next_push()) else None),
            "last_error": self.last_error,
        }


async def async_select_mode(coordinator: FluvalCoordinator, option: str) -> None:
    """Switch to one of the user-facing modes (the light's three, sun or weather sync).

    Picking any mode other than sun sync turns sun sync off, so the nightly
    push doesn't overwrite a schedule the user has taken back control of;
    likewise any mode but weather sync turns weather sync off.
    """
    sun_sync = coordinator.sun_sync
    weather = coordinator.weather_sync
    if option == MODE_WEATHER_SYNC:
        if weather is None:
            raise SunSyncError("not_loaded", "The light isn't set up yet.")
        await weather.async_enable()
        return
    if option == MODE_SUN_SYNC:
        if sun_sync is None:
            raise SunSyncError("not_loaded", "The light isn't set up yet.")
        if weather is not None:
            weather.async_disable()
        await sun_sync.async_enable()
        return
    if sun_sync is not None:
        sun_sync.async_disable()
        await _async_drop_weather_effect(coordinator, sun_sync)
    await coordinator.async_set_mode(MODES_REVERSE[option])


async def _async_drop_weather_effect(coordinator: FluvalCoordinator, sun_sync: SunSync) -> None:
    """Take weather sync's effect off the light's Auto schedule, now that it's off.

    Its effects cover the whole day between them, so one left behind would
    keep playing over the Auto schedule.
    """
    current = coordinator.data.auto_schedule
    if current is None or not is_weather_effect(current):
        return
    dynamic = sun_sync.fallback_dynamic(current)
    await coordinator.async_set_auto_schedule(replace(current, dynamic=dynamic), activate=False)
