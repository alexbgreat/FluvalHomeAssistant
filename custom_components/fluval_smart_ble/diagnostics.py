"""Diagnostics support for the Fluval Smart BLE integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import CONF_MODEL_ID, DOMAIN
from .coordinator import FluvalCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Includes the light's raw BLE advertisement (service/manufacturer data)
    so model-detection issues can be diagnosed without a physical device.
    """
    coordinator: FluvalCoordinator = hass.data[DOMAIN][entry.entry_id]
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
        },
        "current_advertisement": advertisement,
    }
