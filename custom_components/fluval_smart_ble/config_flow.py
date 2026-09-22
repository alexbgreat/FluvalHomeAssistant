"""Config flow for the Fluval Smart BLE integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from bleak.exc import BleakError

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TimeSelector,
)

try:
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # Home Assistant core < 2024.7
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .const import CONF_MODEL_ID, DOMAIN, SERVICE_UUID
from .models import get_model
from .schedule import (
    PRO_MAX_POINTS,
    PRO_MIN_POINTS,
    AutoSchedule,
    ProPoint,
    ProSchedule,
    auto_from_dict,
    auto_to_dict,
    clamp_percent,
    default_auto_schedule,
    default_pro_schedule,
    format_time,
    parse_time,
    pro_from_dict,
    pro_to_dict,
    resize_pro_schedule,
)

if TYPE_CHECKING:
    from .coordinator import FluvalCoordinator

_LOGGER = logging.getLogger(__name__)


def _extract_model_id(discovery_info: BluetoothServiceInfoBleak) -> int | None:
    """Pull the model ID out of the advertisement's manufacturer data.

    Fluval's BLE module doesn't use a real, spec-compliant company ID: it
    broadcasts a 4-character ASCII hex string of the model ID (e.g. "0141"
    for model 321 = 0x141) spread across the two-byte "company ID" field
    and the start of the manufacturer data payload. Confirmed against a
    real Aquasky 600mm's advertisement, whose manufacturer_data was
    {0x3130: bytes.fromhex("3431303130330000...")}  ->  raw bytes
    "0","1","4","1",...  ->  0x0141 == 321 (LIGHT_ID_AQUASKY_600).
    """
    _LOGGER.debug(
        "Fluval light %s advertised manufacturer_data=%s service_data=%s",
        discovery_info.address,
        {hex(k): v.hex() for k, v in discovery_info.manufacturer_data.items()},
        {k: v.hex() for k, v in discovery_info.service_data.items()},
    )
    for company_id, payload in discovery_info.manufacturer_data.items():
        raw = bytes([company_id & 0xFF, (company_id >> 8) & 0xFF]) + payload
        value = 0
        digits = 0
        for byte in raw[:4]:
            if not 0x30 <= byte <= 0x39:  # ASCII '0'-'9'
                break
            value = (value << 4) | (byte - 0x30)
            digits += 1
        if digits == 4:
            return value
    return None


def _describe_model(model_id: int | None) -> str:
    if model_id is not None:
        model = get_model(model_id)
        if model is not None:
            return model.name
    return "Unrecognized model (will be treated as a generic RGBW light)"


class FluvalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fluval Smart BLE lights."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FluvalOptionsFlow:
        """Return the schedule editor shown under the integration's Configure button."""
        return FluvalOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._model_id: int | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovered Fluval light."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self._model_id = _extract_model_id(discovery_info)
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup of a discovered light."""
        assert self._discovery_info is not None
        if user_input is not None:
            return self._async_create_entry()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._discovery_info.name or self._discovery_info.address,
                "model": _describe_model(self._model_id),
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setup by picking a device from the list of already-seen ones."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            await self.async_set_unique_id(discovery_info.address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._discovery_info = discovery_info
            self._model_id = _extract_model_id(discovery_info)
            return self._async_create_entry()

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            if discovery_info.address in current_addresses:
                continue
            if SERVICE_UUID not in discovery_info.service_uuids:
                continue
            self._discovered_devices[discovery_info.address] = discovery_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{info.name or 'Fluval light'} ({address})"
                            for address, info in self._discovered_devices.items()
                        }
                    )
                }
            ),
        )

    def _async_create_entry(self) -> ConfigFlowResult:
        assert self._discovery_info is not None
        model = get_model(self._model_id) if self._model_id is not None else None
        title = model.name if model is not None else (self._discovery_info.name or "Fluval Smart Light")
        return self.async_create_entry(
            title=title,
            data={
                CONF_ADDRESS: self._discovery_info.address,
                CONF_MODEL_ID: self._model_id or 0,
            },
        )


# Options keys holding the last schedules saved from the options flow, so
# the editor can be prefilled across restarts even when the light isn't
# currently in that mode (it only reports the active mode's schedule).
OPT_AUTO_SCHEDULE = "auto_schedule"
OPT_PRO_SCHEDULE = "pro_schedule"

CONF_ACTIVATE = "activate"
CONF_POINT_COUNT = "point_count"
CONF_SUNRISE_START = "sunrise_start"
CONF_SUNRISE_END = "sunrise_end"
CONF_SUNSET_START = "sunset_start"
CONF_SUNSET_END = "sunset_end"
CONF_TURNOFF_ENABLED = "turnoff_enabled"
CONF_TURNOFF = "turnoff"

_PERCENT_SLIDER = NumberSelector(
    NumberSelectorConfig(min=0, max=100, step=1, mode=NumberSelectorMode.SLIDER, unit_of_measurement="%")
)
_PERCENT_BOX = NumberSelector(
    NumberSelectorConfig(min=0, max=100, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="%")
)


def _minutes(value) -> int:
    return value.hour * 60 + value.minute


class FluvalOptionsFlow(OptionsFlow):
    """Edit the light's on-device Auto and Pro schedules.

    The per-channel fields are named after the model's own channels, which
    vary per model and so can't come from the translation files; Home
    Assistant shows such untranslated field keys verbatim as their labels.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Stored under a private name: newer Home Assistant cores provide
        # `self.config_entry` themselves and warn if a flow assigns it.
        self._entry = config_entry
        self._pro_draft: ProSchedule | None = None

    @property
    def _coordinator(self) -> FluvalCoordinator | None:
        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)

    def _channels(self, coordinator: FluvalCoordinator) -> tuple[str, ...]:
        return coordinator.model.channels

    def _save_option(self, key: str, value: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(title="", data={**self._entry.options, key: value})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._coordinator is None:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(step_id="init", menu_options=["auto", "pro_points"])

    # --- Auto mode -----------------------------------------------------------

    def _current_auto(self, coordinator: FluvalCoordinator) -> AutoSchedule:
        count = len(self._channels(coordinator))
        return (
            coordinator.data.auto_schedule
            or auto_from_dict(self._entry.options.get(OPT_AUTO_SCHEDULE), count)
            or default_auto_schedule(count)
        )

    async def async_step_auto(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the sunrise/sunset ramp used in Auto mode."""
        coordinator = self._coordinator
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        channels = self._channels(coordinator)
        current = self._current_auto(coordinator)
        errors: dict[str, str] = {}

        if user_input is not None:
            schedule = AutoSchedule(
                sunrise_start=parse_time(user_input[CONF_SUNRISE_START]),
                sunrise_end=parse_time(user_input[CONF_SUNRISE_END]),
                day=[clamp_percent(user_input[f"Day {name}"]) for name in channels],
                sunset_start=parse_time(user_input[CONF_SUNSET_START]),
                sunset_end=parse_time(user_input[CONF_SUNSET_END]),
                night=[clamp_percent(user_input[f"Night {name}"]) for name in channels],
                turnoff_enabled=user_input[CONF_TURNOFF_ENABLED],
                turnoff=parse_time(user_input[CONF_TURNOFF]),
                dynamic=current.dynamic,
            )
            if _minutes(schedule.sunrise_start) >= _minutes(schedule.sunrise_end):
                errors["base"] = "sunrise_order"
            elif _minutes(schedule.sunset_start) >= _minutes(schedule.sunset_end):
                errors["base"] = "sunset_order"
            elif _minutes(schedule.sunrise_end) > _minutes(schedule.sunset_start):
                errors["base"] = "sunrise_after_sunset"
            else:
                try:
                    await coordinator.async_set_auto_schedule(schedule, user_input[CONF_ACTIVATE])
                except (BleakError, TimeoutError) as err:
                    _LOGGER.warning("Could not program Auto schedule on %s: %s", coordinator.address, err)
                    errors["base"] = "cannot_connect"
                else:
                    return self._save_option(OPT_AUTO_SCHEDULE, auto_to_dict(schedule))
            current = schedule

        fields: dict[Any, Any] = {
            vol.Required(CONF_SUNRISE_START, default=format_time(current.sunrise_start)): TimeSelector(),
            vol.Required(CONF_SUNRISE_END, default=format_time(current.sunrise_end)): TimeSelector(),
        }
        for name, value in zip(channels, current.day):
            fields[vol.Required(f"Day {name}", default=value)] = _PERCENT_SLIDER
        fields[vol.Required(CONF_SUNSET_START, default=format_time(current.sunset_start))] = TimeSelector()
        fields[vol.Required(CONF_SUNSET_END, default=format_time(current.sunset_end))] = TimeSelector()
        for name, value in zip(channels, current.night):
            fields[vol.Required(f"Night {name}", default=value)] = _PERCENT_SLIDER
        fields[vol.Required(CONF_TURNOFF_ENABLED, default=current.turnoff_enabled)] = BooleanSelector()
        fields[vol.Required(CONF_TURNOFF, default=format_time(current.turnoff))] = TimeSelector()
        fields[vol.Required(CONF_ACTIVATE, default=True)] = BooleanSelector()

        return self.async_show_form(step_id="auto", data_schema=vol.Schema(fields), errors=errors)

    # --- Pro mode ------------------------------------------------------------

    def _current_pro(self, coordinator: FluvalCoordinator) -> ProSchedule:
        count = len(self._channels(coordinator))
        return (
            coordinator.data.pro_schedule
            or pro_from_dict(self._entry.options.get(OPT_PRO_SCHEDULE), count)
            or default_pro_schedule(count)
        )

    async def async_step_pro_points(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick how many points the Pro schedule has (the next form has one row per point)."""
        coordinator = self._coordinator
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        current = self._current_pro(coordinator)

        if user_input is not None:
            count = int(user_input[CONF_POINT_COUNT])
            self._pro_draft = resize_pro_schedule(current, len(self._channels(coordinator)), count)
            return await self.async_step_pro()

        return self.async_show_form(
            step_id="pro_points",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POINT_COUNT, default=len(current.points)): NumberSelector(
                        NumberSelectorConfig(
                            min=PRO_MIN_POINTS, max=PRO_MAX_POINTS, step=1, mode=NumberSelectorMode.SLIDER
                        )
                    )
                }
            ),
        )

    async def async_step_pro(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit each Pro schedule point's time and per-channel brightness."""
        coordinator = self._coordinator
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        channels = self._channels(coordinator)
        draft = self._pro_draft or self._current_pro(coordinator)
        errors: dict[str, str] = {}

        if user_input is not None:
            points = [
                ProPoint(
                    parse_time(user_input[f"Point {i} time"]),
                    [clamp_percent(user_input[f"Point {i} {name}"]) for name in channels],
                )
                for i in range(1, len(draft.points) + 1)
            ]
            schedule = ProSchedule(points, draft.dynamic)
            if len({_minutes(p.at) for p in points}) != len(points):
                errors["base"] = "duplicate_times"
            else:
                try:
                    await coordinator.async_set_pro_schedule(schedule, user_input[CONF_ACTIVATE])
                except (BleakError, TimeoutError) as err:
                    _LOGGER.warning("Could not program Pro schedule on %s: %s", coordinator.address, err)
                    errors["base"] = "cannot_connect"
                else:
                    return self._save_option(OPT_PRO_SCHEDULE, pro_to_dict(schedule))
            # Redisplay what was entered, in the order it was entered.
            draft = schedule
            self._pro_draft = schedule

        fields: dict[Any, Any] = {}
        for i, point in enumerate(draft.points, start=1):
            fields[vol.Required(f"Point {i} time", default=format_time(point.at))] = TimeSelector()
            for name, value in zip(channels, point.values):
                fields[vol.Required(f"Point {i} {name}", default=value)] = _PERCENT_BOX
        fields[vol.Required(CONF_ACTIVATE, default=True)] = BooleanSelector()

        return self.async_show_form(
            step_id="pro",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"count": str(len(draft.points))},
        )
