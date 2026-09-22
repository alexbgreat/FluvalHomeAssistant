"""Mode selection for the Fluval Smart BLE integration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODE_OPTIONS
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity
from .sun_sync import SunSyncError, async_select_mode


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the light's operating mode select entity."""
    coordinator: FluvalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FluvalModeSelect(coordinator)])


class FluvalModeSelect(FluvalEntity, SelectEntity):
    """Switch the light between manual, auto (sunrise/sunset), pro and sun sync.

    Sun sync is Home Assistant's own mode: the light runs in auto, with its
    schedule rewritten nightly to follow the real sunrise and sunset.
    """

    _attr_name = "Mode"
    _attr_icon = "mdi:tune"
    _attr_options = MODE_OPTIONS

    def __init__(self, coordinator: FluvalCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_mode"

    @property
    def current_option(self) -> str | None:
        return self.coordinator.effective_mode

    async def async_select_option(self, option: str) -> None:
        try:
            await async_select_mode(self.coordinator, option)
        except SunSyncError as err:
            raise HomeAssistantError(str(err)) from err
