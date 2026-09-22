"""Buttons for the Fluval Smart BLE integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the find-device button."""
    coordinator: FluvalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FluvalFindButton(coordinator)])


class FluvalFindButton(FluvalEntity, ButtonEntity):
    """Makes the light blink so it can be located."""

    _attr_name = "Find"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: FluvalCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_find"

    async def async_press(self) -> None:
        await self.coordinator.async_find()
