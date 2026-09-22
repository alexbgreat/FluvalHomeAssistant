"""Mode selection for the Fluval Smart BLE integration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODES, MODES_REVERSE
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the light's operating mode select entity."""
    coordinator: FluvalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FluvalModeSelect(coordinator)])


class FluvalModeSelect(FluvalEntity, SelectEntity):
    """Switch the light between its manual, auto (sunrise/sunset) and pro schedules."""

    _attr_name = "Mode"
    _attr_icon = "mdi:tune"
    _attr_options = list(MODES.values())

    def __init__(self, coordinator: FluvalCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_mode"

    @property
    def current_option(self) -> str | None:
        mode = self.coordinator.data.mode
        return MODES.get(mode) if mode is not None else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(MODES_REVERSE[option])
