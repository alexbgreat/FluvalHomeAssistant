"""Weather sync mode: sun sync, with the light's effect following the weather.

The light's Auto schedule carries a single dynamic effect with a daily
window. Weather sync sits on top of sun sync: whenever the chosen weather
entity's condition changes, and at each day/night boundary of the pushed
schedule, Home Assistant rewrites that effect - the one mapped to the
current weather during the day, moonlight (or the weather's effect) at
night - and pushes the schedule to the light again.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from bleak.exc import BleakError

import homeassistant.util.dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_state_change_event,
)

from .coordinator import FluvalCoordinator
from .schedule import (
    AutoSchedule,
    WeatherSyncConfig,
    build_weather_effect,
    is_night,
    weather_effect_id,
    weather_group,
    weather_sync_from_dict,
    weather_sync_to_dict,
)
from .sun_sync import MAX_RETRIES, RETRY_DELAY, SunSyncError

_LOGGER = logging.getLogger(__name__)

OPT_WEATHER_SYNC = "weather_sync"


class WeatherSync:
    """Owns one light's weather sync settings, weather listener and timers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: FluvalCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        stored = entry.options.get(OPT_WEATHER_SYNC) or {}
        self.config: WeatherSyncConfig | None = weather_sync_from_dict(stored.get("config"))
        self.enabled: bool = bool(stored.get("enabled")) and self.config is not None
        self.last_update: datetime | None = None
        self.last_error: str | None = None
        self._condition: str | None = None
        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_boundary: CALLBACK_TYPE | None = None
        self._unsub_retry: CALLBACK_TYPE | None = None
        self._retries = 0

    @property
    def active(self) -> bool:
        """On, and so is the sun sync it rides on."""
        sun_sync = self.coordinator.sun_sync
        return self.enabled and sun_sync is not None and sun_sync.enabled

    # --- lifecycle ------------------------------------------------------------

    @callback
    def async_start(self) -> None:
        """Start following the weather entity (sun sync makes the first push)."""
        if not self.enabled or self.config is None:
            return
        self._subscribe()
        self._arm_boundary(self._current_schedule())

    @callback
    def async_stop(self) -> None:
        for unsub in (self._unsub_state, self._unsub_boundary, self._unsub_retry):
            if unsub is not None:
                unsub()
        self._unsub_state = self._unsub_boundary = self._unsub_retry = None

    @callback
    def _subscribe(self) -> None:
        self.async_stop()
        assert self.config is not None
        self._condition = self._read_condition()
        self._unsub_state = async_track_state_change_event(
            self.hass, [self.config.entity_id], self._handle_state
        )

    def _read_condition(self) -> str | None:
        if self.config is None or (state := self.hass.states.get(self.config.entity_id)) is None:
            return None
        return None if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE) else state.state

    @callback
    def _handle_state(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        # A briefly unavailable weather service keeps the last known weather.
        if new_state is None or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        if new_state.state == self._condition:
            return  # only an attribute (temperature, ...) changed
        _LOGGER.debug(
            "Weather for %s changed from %s to %s", self.coordinator.address, self._condition, new_state.state
        )
        self._condition = new_state.state
        self._retries = 0
        self.hass.async_create_task(self._async_update())

    @callback
    def _handle_boundary(self, _now: datetime) -> None:
        self._unsub_boundary = None
        self._retries = 0
        self.hass.async_create_task(self._async_update())

    @callback
    def _handle_retry(self, _now: datetime) -> None:
        self._unsub_retry = None
        self.hass.async_create_task(self._async_update())

    def _current_schedule(self) -> AutoSchedule | None:
        """The Auto schedule on the light, as last read back or pushed."""
        if self.coordinator.data.auto_schedule is not None:
            return self.coordinator.data.auto_schedule
        sun_sync = self.coordinator.sun_sync
        return sun_sync.last_schedule if sun_sync is not None else None

    @staticmethod
    def next_boundary(schedule: AutoSchedule, now: datetime) -> datetime:
        """The next start of the sunrise fade or end of the sunset fade."""
        candidates = []
        for at in (schedule.sunrise_start, schedule.sunset_end):
            when = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
            candidates.append(when if when > now else when + timedelta(days=1))
        return min(candidates)

    @callback
    def _arm_boundary(self, schedule: AutoSchedule | None) -> None:
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None
        if not self.active or schedule is None:
            return
        self._unsub_boundary = async_track_point_in_time(
            self.hass, self._handle_boundary, self.next_boundary(schedule, dt_util.now())
        )

    # --- settings ---------------------------------------------------------------

    @callback
    def _save(self) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                OPT_WEATHER_SYNC: {
                    "enabled": self.enabled,
                    "config": weather_sync_to_dict(self.config) if self.config is not None else None,
                },
            },
        )
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_enable(self, config: WeatherSyncConfig | None = None) -> AutoSchedule:
        """Turn weather sync (and the sun sync under it) on, and push right away."""
        config = config or self.config
        if config is None:
            raise SunSyncError("not_configured", "Pick a weather entity for weather sync first.")
        if self.hass.states.get(config.entity_id) is None:
            raise SunSyncError("unknown_entity", f"The weather entity {config.entity_id} doesn't exist.")
        sun_sync = self.coordinator.sun_sync
        if sun_sync is None:
            raise SunSyncError("not_loaded", "The light isn't set up yet.")
        self.config = config
        self.enabled = True
        self._retries = 0
        self._subscribe()
        try:
            return await sun_sync.async_enable()
        except SunSyncError:
            # Sun sync refused its settings, so it's still off, and so is this.
            if not sun_sync.enabled:
                self.enabled = False
                self.async_stop()
            raise
        finally:
            # Saved even if the light couldn't be reached: sun sync retries.
            self._save()

    @callback
    def async_disable(self) -> None:
        if not self.enabled:
            return
        self.enabled = False
        self.async_stop()
        self._save()

    # --- computing and pushing --------------------------------------------------

    def dynamic_for(self, schedule: AutoSchedule, now: datetime | None = None) -> bytes:
        """The effect block `schedule` should carry for the weather at `now`."""
        assert self.config is not None
        night = is_night(schedule, (now or dt_util.now()).time())
        effect_id = weather_effect_id(self.config, self._condition, night)
        return build_weather_effect(effect_id, schedule, night).to_bytes()

    @callback
    def async_pushed(self, schedule: AutoSchedule) -> None:
        """Sun sync pushed `schedule` (with our effect): note it, re-arm the boundary."""
        self.last_update = dt_util.now()
        self.last_error = None
        self._arm_boundary(schedule)

    async def _async_update(self) -> None:
        """Re-push the light's schedule if the effect it should carry has changed.

        Only the effect changes: the fades stay as sun sync last pushed them
        (re-running sun sync after noon would move them to tomorrow's sun).
        """
        sun_sync = self.coordinator.sun_sync
        if not self.active or sun_sync is None:
            return
        current = self._current_schedule()
        try:
            if current is None:
                await sun_sync.async_push()
                return
            dynamic = self.dynamic_for(current)
            if current.dynamic == dynamic:
                self._arm_boundary(current)
                return
            await self.coordinator.async_set_auto_schedule(replace(current, dynamic=dynamic), activate=False)
        except SunSyncError as err:
            self.last_error = str(err)
            _LOGGER.warning("Weather sync for %s skipped: %s", self.coordinator.address, err)
            self._arm_boundary(current)
            return
        except (BleakError, TimeoutError) as err:
            self.last_error = f"Could not reach the light: {err}"
            self.coordinator.async_set_updated_data(self.coordinator.data)
            if self._retries >= MAX_RETRIES:
                _LOGGER.warning("Weather sync for %s failed, giving up: %s", self.coordinator.address, err)
                self._arm_boundary(current)
                return
            self._retries += 1
            _LOGGER.debug("Weather sync push to %s failed (%s), retrying", self.coordinator.address, err)
            self._unsub_retry = async_call_later(self.hass, RETRY_DELAY, self._handle_retry)
            return
        _LOGGER.debug("Weather sync for %s: effect now %s", self.coordinator.address, dynamic.hex())
        self.async_pushed(self.coordinator.data.auto_schedule)

    def describe(self) -> dict[str, Any]:
        """State for the panel."""
        config = self.config or WeatherSyncConfig(entity_id="")
        schedule = self._current_schedule()
        # While off nothing tracks the entity, so read its weather directly.
        condition = self._condition if self._unsub_state is not None else self._read_condition()
        info: dict[str, Any] = {
            "enabled": self.active,
            "config": weather_sync_to_dict(config),
            "condition": condition,
            "group": weather_group(condition),
            "period": None,
            "effect": None,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "next_change": None,
            "last_error": self.last_error,
        }
        if self.config is not None and schedule is not None:
            now = dt_util.now()
            night = is_night(schedule, now.time())
            info.update(
                period="night" if night else "day",
                effect=weather_effect_id(self.config, condition, night),
                next_change=self.next_boundary(schedule, now).isoformat(),
            )
        return info
