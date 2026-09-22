"""Config flow for the Fluval Smart BLE integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ADDRESS

try:
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # Home Assistant core < 2024.7
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .const import CONF_MODEL_ID, DOMAIN, SERVICE_UUID
from .models import get_model

_LOGGER = logging.getLogger(__name__)


def _extract_model_id(discovery_info: BluetoothServiceInfoBleak) -> int | None:
    """Pull the 2-byte model ID out of the advertisement's manufacturer data."""
    _LOGGER.debug(
        "Fluval light %s advertised manufacturer_data=%s service_data=%s",
        discovery_info.address,
        {hex(k): v.hex() for k, v in discovery_info.manufacturer_data.items()},
        {k: v.hex() for k, v in discovery_info.service_data.items()},
    )
    for payload in discovery_info.manufacturer_data.values():
        if len(payload) >= 2:
            return (payload[0] << 8) | payload[1]
    return None


def _describe_model(model_id: int | None) -> str:
    if model_id is not None:
        model = get_model(model_id)
        if model is not None:
            return model.name
    return "Unrecognized model (will be treated as a generic RGBW light)"


class FluvalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fluval Smart BLE lights."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._model_id: int | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovered Fluval light."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self._model_id = _extract_model_id(discovery_info)
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup of a discovered light."""
        assert self._discovery_info is not None
        if user_input is not None:
            return self._async_create_entry()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._discovery_info.name or self._discovery_info.address,
                "model": _describe_model(self._model_id),
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setup by picking a device from the list of already-seen ones."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            await self.async_set_unique_id(discovery_info.address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._discovery_info = discovery_info
            self._model_id = _extract_model_id(discovery_info)
            return self._async_create_entry()

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            if discovery_info.address in current_addresses:
                continue
            if SERVICE_UUID not in discovery_info.service_uuids:
                continue
            self._discovered_devices[discovery_info.address] = discovery_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{info.name or 'Fluval light'} ({address})"
                            for address, info in self._discovered_devices.items()
                        }
                    )
                }
            ),
        )

    def _async_create_entry(self) -> ConfigFlowResult:
        assert self._discovery_info is not None
        model = get_model(self._model_id) if self._model_id is not None else None
        title = model.name if model is not None else (self._discovery_info.name or "Fluval Smart Light")
        return self.async_create_entry(
            title=title,
            data={
                CONF_ADDRESS: self._discovery_info.address,
                CONF_MODEL_ID: self._model_id or 0,
            },
        )
