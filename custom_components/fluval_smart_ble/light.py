"""Light platform for the Fluval Smart BLE integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Fluval light entity."""
    coordinator: FluvalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FluvalLight(coordinator)])


class FluvalLight(FluvalEntity, LightEntity):
    """Master on/off + brightness (and RGBW color, where supported) entity."""

    _attr_name = None

    def __init__(self, coordinator: FluvalCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_light"
        if coordinator.model.is_rgbw:
            self._attr_supported_color_modes = {ColorMode.RGBW}
            self._attr_color_mode = ColorMode.RGBW
        else:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.is_on

    @property
    def _channel_bytes(self) -> list[int]:
        """Current channel percentages (0-100) scaled to 0-255."""
        return [
            round(max(0.0, min(100.0, pct)) * 255 / 100)
            for pct in self.coordinator.data.channel_values
        ]

    @property
    def brightness(self) -> int | None:
        channels = self._channel_bytes
        return max(channels) if channels else 0

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        if not self.coordinator.model.is_rgbw:
            return None
        channels = self._channel_bytes
        peak = max(channels) if channels else 0
        if peak == 0:
            return (0, 0, 0, 0)
        normalized = tuple(min(255, round(value * 255 / peak)) for value in channels)
        return normalized  # type: ignore[return-value]

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_on()

        if ColorMode.RGBW in self._attr_supported_color_modes and "rgbw_color" in kwargs:
            r, g, b, w = kwargs["rgbw_color"]
            await self.coordinator.async_set_channels(
                [value * 100 / 255 for value in (r, g, b, w)]
            )
        elif "brightness" in kwargs:
            new_brightness: int = kwargs["brightness"]
            current = self._channel_bytes
            peak = max(current) if current else 0
            if peak == 0:
                values = [new_brightness * 100 / 255] * len(current)
            else:
                values = [
                    min(100.0, value * new_brightness / peak * 100 / 255)
                    for value in current
                ]
            await self.coordinator.async_set_channels(values)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_off()
