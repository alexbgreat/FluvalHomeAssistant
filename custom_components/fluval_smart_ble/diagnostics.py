"""Diagnostics support for the Fluval Smart BLE integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import CONF_MODEL_ID, DOMAIN
from .coordinator import FluvalCoordinator
from .schedule import (
    auto_to_dict,
    decode_effect,
    effect_to_dict,
    is_weather_effect,
    pro_to_dict,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Includes the light's raw BLE advertisement (service/manufacturer data)
    so model-detection issues can be diagnosed without a physical device,
    and its schedules, effects, sun/weather sync settings and recent frames
    so schedule issues can be too.
    """
    coordinator: FluvalCoordinator = hass.data[DOMAIN][entry.entry_id]
    auto, pro = coordinator.data.auto_schedule, coordinator.data.pro_schedule
    address = entry.data[CONF_ADDRESS]

    service_info = bluetooth.async_last_service_info(hass, address, connectable=True)
    advertisement: dict[str, Any] | None = None
    if service_info is not None:
        advertisement = {
            "name": service_info.name,
            "rssi": service_info.rssi,
            "service_uuids": list(service_info.service_uuids),
            "service_data": {
                uuid: data.hex() for uuid, data in service_info.service_data.items()
            },
            "manufacturer_data": {
                hex(company_id): data.hex()
                for company_id, data in service_info.manufacturer_data.items()
            },
        }

    return {
        "entry_data": {
            CONF_ADDRESS: address,
            CONF_MODEL_ID: entry.data.get(CONF_MODEL_ID),
        },
        "resolved_model": {
            "model_id": coordinator.model.model_id,
            "name": coordinator.model.name,
            "channels": list(coordinator.model.channels),
            "is_rgbw": coordinator.model.is_rgbw,
        },
        "coordinator_state": {
            "is_on": coordinator.data.is_on,
            "mode": coordinator.data.mode,
            "channel_values": coordinator.data.channel_values,
            "last_update_success": coordinator.last_update_success,
            "effective_mode": coordinator.effective_mode,
            "last_read_frame": coordinator.last_read_frame.hex() if coordinator.last_read_frame else None,
        },
        "auto_schedule": auto_to_dict(auto) if auto is not None else None,
        "auto_effect": effect_to_dict(decode_effect(auto.dynamic)) if auto is not None else None,
        "auto_effect_is_weather": is_weather_effect(auto) if auto is not None else None,
        "pro_schedule": pro_to_dict(pro) if pro is not None else None,
        "options": dict(entry.options),
        "sun_sync": (
            coordinator.sun_sync.describe(auto) if coordinator.sun_sync is not None and auto is not None else None
        ),
        "weather_sync": coordinator.weather_sync.describe() if coordinator.weather_sync is not None else None,
        "recent_writes": [{"at": at, "frame": frame} for at, frame in coordinator.recent_writes],
        "current_advertisement": advertisement,
    }
