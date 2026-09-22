"""Shared base entity for the Fluval Smart BLE integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FluvalCoordinator


class FluvalEntity(CoordinatorEntity[FluvalCoordinator]):
    """Base entity tying a platform entity to a Fluval light's coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FluvalCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=coordinator.model.name,
            manufacturer="Fluval",
            model=coordinator.model.name,
        )
