"""Websocket API backing the schedule editor sidebar panel."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from bleak.exc import BleakError

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, MODE_OPTIONS
from .coordinator import FluvalCoordinator
from .schedule import (
    EFFECTS,
    AutoSchedule,
    ProSchedule,
    auto_from_dict,
    auto_to_dict,
    default_auto_schedule,
    default_pro_schedule,
    decode_effect,
    effect_from_dict,
    effect_to_dict,
    pro_from_dict,
    pro_to_dict,
    sun_sync_from_dict,
    validate_auto,
    validate_effect,
    validate_pro,
)
from .sun_sync import SunSyncError, async_select_mode

_LOGGER = logging.getLogger(__name__)

# Config entry options keys holding the last schedules saved from the panel,
# so it can be prefilled across restarts even when the light isn't
# currently in that mode (it only reports the active mode's schedule).
OPT_AUTO_SCHEDULE = "auto_schedule"
OPT_PRO_SCHEDULE = "pro_schedule"

_ERROR_MESSAGES = {
    "sunrise_order": "Sunrise must start before it ends.",
    "sunset_order": "Sunset must start before it ends.",
    "sunrise_after_sunset": "Sunrise must end before sunset starts.",
    "duplicate_times": "Each point needs a different time of day.",
    "effect_unknown": "Pick an effect to play.",
    "effect_no_days": "Pick at least one day for the effect.",
    "effect_window": "The effect's start and end times must differ.",
}


@callback
def async_register_api(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_lights)
    websocket_api.async_register_command(hass, ws_set_auto)
    websocket_api.async_register_command(hass, ws_set_pro)
    websocket_api.async_register_command(hass, ws_set_mode)
    websocket_api.async_register_command(hass, ws_set_sun_sync)
    websocket_api.async_register_command(hass, ws_play_effect)


def _coordinator(hass: HomeAssistant, entry: ConfigEntry) -> FluvalCoordinator | None:
    if entry.state is not ConfigEntryState.LOADED:
        return None
    return hass.data.get(DOMAIN, {}).get(entry.entry_id)


def _current_auto(coordinator: FluvalCoordinator, entry: ConfigEntry) -> tuple[AutoSchedule, str]:
    count = len(coordinator.model.channels)
    if coordinator.data.auto_schedule is not None:
        return coordinator.data.auto_schedule, "light"
    if (saved := auto_from_dict(entry.options.get(OPT_AUTO_SCHEDULE), count)) is not None:
        return saved, "saved"
    return default_auto_schedule(count), "default"


def _current_pro(coordinator: FluvalCoordinator, entry: ConfigEntry) -> tuple[ProSchedule, str]:
    count = len(coordinator.model.channels)
    if coordinator.data.pro_schedule is not None:
        return coordinator.data.pro_schedule, "light"
    if (saved := pro_from_dict(entry.options.get(OPT_PRO_SCHEDULE), count)) is not None:
        return saved, "saved"
    return default_pro_schedule(count), "default"


def _describe(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = _coordinator(hass, entry)
    info: dict[str, Any] = {"entry_id": entry.entry_id, "title": entry.title, "loaded": coordinator is not None}
    if coordinator is None:
        return info
    auto, auto_source = _current_auto(coordinator, entry)
    pro, pro_source = _current_pro(coordinator, entry)
    auto_dict, pro_dict = auto_to_dict(auto), pro_to_dict(pro)
    # The raw dynamic-effect bytes are replaced by their decoded form.
    auto_dict.pop("dynamic"), pro_dict.pop("dynamic")
    info.update(
        address=coordinator.address,
        model=coordinator.model.name,
        channels=list(coordinator.model.channels),
        mode=coordinator.effective_mode,
        auto=auto_dict,
        auto_source=auto_source,
        auto_effect=effect_to_dict(decode_effect(auto.dynamic)),
        pro=pro_dict,
        pro_source=pro_source,
        pro_effect=effect_to_dict(decode_effect(pro.dynamic)),
        sun_sync=coordinator.sun_sync.describe(auto) if coordinator.sun_sync is not None else None,
    )
    return info


def _lookup(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]):
    """Resolve the message's entry_id to a loaded light, or send an error and return None."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "Unknown light.")
        return None
    coordinator = _coordinator(hass, entry)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "The light isn't connected yet.")
        return None
    return entry, coordinator


def _send_invalid(connection: websocket_api.ActiveConnection, msg: dict[str, Any], code: str) -> None:
    connection.send_error(msg["id"], code, _ERROR_MESSAGES.get(code, "Invalid schedule."))


def _parse_effect(connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> tuple[bool, bytes | None]:
    """Decode the message's optional "effect"; (False, None) after sending an error."""
    if msg.get("effect") is None:
        return True, None
    effect = effect_from_dict(msg["effect"])
    if effect is None:
        _send_invalid(connection, msg, "invalid_format")
        return False, None
    if (error := validate_effect(effect)) is not None:
        _send_invalid(connection, msg, error)
        return False, None
    return True, effect.to_bytes()


def _send_bluetooth_error(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any], coordinator: FluvalCoordinator, err: Exception
) -> None:
    _LOGGER.warning("Could not send to Fluval light %s: %s", coordinator.address, err)
    connection.send_error(
        msg["id"],
        "cannot_connect",
        "Could not reach the light. Make sure it is powered on and in Bluetooth range, then try again.",
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/lights", vol.Optional("refresh", default=False): bool}
)
@websocket_api.async_response
async def ws_lights(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """List configured lights with their channels, mode and schedules."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if msg["refresh"]:
        for entry in entries:
            if (coordinator := _coordinator(hass, entry)) is not None:
                await coordinator.async_refresh()
    connection.send_result(
        msg["id"],
        {
            "lights": [_describe(hass, entry) for entry in entries],
            "effects": [{"id": effect_id, "name": name} for effect_id, name in EFFECTS.items()],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_auto",
        vol.Required("entry_id"): str,
        vol.Required("schedule"): dict,
        vol.Optional("effect"): vol.Any(dict, None),
        vol.Optional("activate", default=True): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_auto(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Program the Auto (sunrise/sunset) schedule."""
    if (found := _lookup(hass, connection, msg)) is None:
        return
    entry, coordinator = found
    schedule = auto_from_dict(msg["schedule"], len(coordinator.model.channels))
    if schedule is None:
        _send_invalid(connection, msg, "invalid_format")
        return
    if (error := validate_auto(schedule)) is not None:
        _send_invalid(connection, msg, error)
        return
    ok, dynamic = _parse_effect(connection, msg)
    if not ok:
        return
    # Without an edited effect, keep the one the schedule already has.
    schedule.dynamic = dynamic if dynamic is not None else _current_auto(coordinator, entry)[0].dynamic
    try:
        await coordinator.async_set_auto_schedule(schedule, msg["activate"])
    except (BleakError, TimeoutError) as err:
        _send_bluetooth_error(connection, msg, coordinator, err)
        return
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_AUTO_SCHEDULE: auto_to_dict(schedule)}
    )
    # A hand-made Auto schedule would be overwritten by the next nightly push.
    if coordinator.sun_sync is not None:
        coordinator.sun_sync.async_disable()
    connection.send_result(msg["id"], _describe(hass, entry))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_pro",
        vol.Required("entry_id"): str,
        vol.Required("schedule"): dict,
        vol.Optional("effect"): vol.Any(dict, None),
        vol.Optional("activate", default=True): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_pro(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Program the Pro (multi-point) schedule."""
    if (found := _lookup(hass, connection, msg)) is None:
        return
    entry, coordinator = found
    schedule = pro_from_dict(msg["schedule"], len(coordinator.model.channels))
    if schedule is None:
        _send_invalid(connection, msg, "invalid_format")
        return
    if (error := validate_pro(schedule)) is not None:
        _send_invalid(connection, msg, error)
        return
    ok, dynamic = _parse_effect(connection, msg)
    if not ok:
        return
    # Without an edited effect, keep the one the schedule already has.
    schedule.dynamic = dynamic if dynamic is not None else _current_pro(coordinator, entry)[0].dynamic
    schedule.points = schedule.sorted_points()
    try:
        await coordinator.async_set_pro_schedule(schedule, msg["activate"])
    except (BleakError, TimeoutError) as err:
        _send_bluetooth_error(connection, msg, coordinator, err)
        return
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_PRO_SCHEDULE: pro_to_dict(schedule)}
    )
    if msg["activate"] and coordinator.sun_sync is not None:
        coordinator.sun_sync.async_disable()
    connection.send_result(msg["id"], _describe(hass, entry))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_mode",
        vol.Required("entry_id"): str,
        vol.Required("mode"): vol.In(MODE_OPTIONS),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_mode(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Switch the light between Manual, Auto, Pro and Sun sync."""
    if (found := _lookup(hass, connection, msg)) is None:
        return
    entry, coordinator = found
    try:
        await async_select_mode(coordinator, msg["mode"])
    except SunSyncError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    except (BleakError, TimeoutError) as err:
        _send_bluetooth_error(connection, msg, coordinator, err)
        return
    connection.send_result(msg["id"], _describe(hass, entry))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_sun_sync",
        vol.Required("entry_id"): str,
        vol.Required("config"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_sun_sync(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Save sun sync settings, turn it on, and push the resulting schedule now."""
    if (found := _lookup(hass, connection, msg)) is None:
        return
    entry, coordinator = found
    config = sun_sync_from_dict(msg["config"], len(coordinator.model.channels))
    if config is None or coordinator.sun_sync is None:
        _send_invalid(connection, msg, "invalid_format")
        return
    if config.effect is not None and (error := validate_effect(config.effect)) is not None:
        _send_invalid(connection, msg, error)
        return
    try:
        await coordinator.sun_sync.async_enable(config)
    except SunSyncError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    except (BleakError, TimeoutError) as err:
        _LOGGER.warning("Could not send to Fluval light %s: %s", coordinator.address, err)
        connection.send_error(
            msg["id"],
            "cannot_connect",
            "Sun sync is on, but the light couldn't be reached to push today's schedule. "
            "It will keep retrying in the background.",
        )
        return
    connection.send_result(msg["id"], _describe(hass, entry))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/play_effect",
        vol.Required("entry_id"): str,
        vol.Required("effect"): vol.In(list(EFFECTS)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_play_effect(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Ask the light to play a dynamic effect now, to preview it (experimental)."""
    if (found := _lookup(hass, connection, msg)) is None:
        return
    _entry, coordinator = found
    try:
        await coordinator.async_play_effect(msg["effect"])
    except (BleakError, TimeoutError) as err:
        _send_bluetooth_error(connection, msg, coordinator, err)
        return
    connection.send_result(msg["id"], {})
