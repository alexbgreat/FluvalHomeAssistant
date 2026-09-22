"""Buttons for the Fluval Smart BLE integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the find-device and sync-time buttons."""
    coordinator: FluvalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FluvalFindButton(coordinator), FluvalSyncTimeButton(coordinator)])


class FluvalFindButton(FluvalEntity, ButtonEntity):
    """Makes the light blink so it can be located."""

    _attr_name = "Find"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: FluvalCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_find"

    async def async_press(self) -> None:
        await self.coordinator.async_find()


class FluvalSyncTimeButton(FluvalEntity, ButtonEntity):
    """Pushes Home Assistant's current local time to the light's clock.

    The light's Auto/Pro schedules run against its own on-board clock,
    which drifts and resets on power loss; this is also done
    automatically on every (re)connect, but is exposed here too so it
    can be triggered on demand or from an automation.
    """

    _attr_name = "Sync Time"
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FluvalCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_sync_time"

    async def async_press(self) -> None:
        await self.coordinator.async_sync_time()
