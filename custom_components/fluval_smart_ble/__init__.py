"""The Fluval Smart BLE integration."""
from __future__ import annotations

from bleak.exc import BleakError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_MODEL_ID, DOMAIN
from .coordinator import FluvalCoordinator
from .models import GENERIC_MODEL, get_model

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.NUMBER, Platform.SELECT, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Fluval Smart light from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    model = get_model(entry.data[CONF_MODEL_ID]) or GENERIC_MODEL

    coordinator = FluvalCoordinator(hass, address, model)
    try:
        await coordinator.async_setup()
    except (BleakError, TimeoutError) as err:
        raise ConfigEntryNotReady(
            f"Could not connect to Fluval light {address}: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Fluval Smart light config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: FluvalCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_unload()
    return unload_ok
