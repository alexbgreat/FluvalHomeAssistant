"""Sidebar panel hosting the light schedule editor."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .api import async_register_api
from .const import DOMAIN

try:
    from homeassistant.components.http import StaticPathConfig
except ImportError:  # Home Assistant core < 2024.7
    StaticPathConfig = None

PANEL_URL_PATH = "fluval-schedule"
PANEL_COMPONENT = "fluval-schedule-panel"
PANEL_TITLE = "Aquarium Light"
PANEL_ICON = "mdi:fishbowl-outline"

_STATIC_URL = f"/{DOMAIN}_static"
_FRONTEND_DIR = Path(__file__).parent / "frontend"
# Static routes and websocket commands can't be unregistered, so they're
# set up once per Home Assistant run no matter how often entries reload.
_DATA_REGISTERED = f"{DOMAIN}_frontend_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Add the schedule editor to the sidebar (idempotent)."""
    if not hass.data.get(_DATA_REGISTERED):
        if StaticPathConfig is not None:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(_STATIC_URL, str(_FRONTEND_DIR), False)]
            )
        else:
            hass.http.register_static_path(_STATIC_URL, str(_FRONTEND_DIR), cache_headers=False)
        async_register_api(hass)
        hass.data[_DATA_REGISTERED] = True

    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    # Cache-bust the module on upgrades.
    version = (await async_get_integration(hass, DOMAIN)).version
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{_STATIC_URL}/{PANEL_COMPONENT}.js?v={version}",
        require_admin=True,
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the schedule editor from the sidebar."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
