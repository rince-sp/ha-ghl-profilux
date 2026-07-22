"""The ProfiLux integration.

Reads all sensors and the state of every power socket from a GHL ProfiLux
controller (ProfiLux 3/4 over the HTTP ``communication.php`` interface, or a
ProfiLux mini over WebSocket) and exposes them as native Home Assistant
entities.
"""
from __future__ import annotations

import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ProfiluxCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH]

STRATEGY_URL = "/profilux_frontend/profilux-strategy.js"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the dashboard-strategy JS and load it in the frontend (once)."""
    if hass.data.get(f"{DOMAIN}_frontend_registered"):
        return
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        js_path = os.path.join(os.path.dirname(__file__), "frontend", "profilux-strategy.js")
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STRATEGY_URL, js_path, False)]
        )
        add_extra_js_url(hass, STRATEGY_URL)
        hass.data[f"{DOMAIN}_frontend_registered"] = True
    except Exception as err:  # noqa: BLE001 - never fail setup over the optional dashboard
        _LOGGER.warning("Could not register ProfiLux dashboard strategy: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ProfiLux from a config entry."""
    coordinator = ProfiluxCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await _async_register_frontend(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Reload when options (e.g. socket control) change so switches appear/vanish.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
