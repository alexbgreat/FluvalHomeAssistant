"""Per-channel brightness controls for the Fluval Smart BLE integration."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODE_MANUAL
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one channel brightness entity per LED channel."""
    coordinator: FluvalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        FluvalChannelNumber(coordinator, index, channel_name)
        for index, channel_name in enumerate(coordinator.model.channels)
    )


class FluvalChannelNumber(FluvalEntity, NumberEntity):
    """A single LED channel's brightness, matching the app's manual sliders."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:led-strip-variant"

    def __init__(self, coordinator: FluvalCoordinator, index: int, channel_name: str) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_name = channel_name
        self._attr_unique_id = f"{coordinator.address}_channel_{index}"

    @property
    def available(self) -> bool:
        mode = self.coordinator.data.mode
        return super().available and mode in (None, MODE_MANUAL)

    @property
    def native_value(self) -> float | None:
        values = self.coordinator.data.channel_values
        if self._index >= len(values):
            return None
        return values[self._index]

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_channel(self._index, value)
