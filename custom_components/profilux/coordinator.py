"""DataUpdateCoordinator for the ProfiLux integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_INTERFACE, DOMAIN, SCAN_INTERVAL
from .protocol import INTERFACE_HTTP, ProfiluxError, fetch_all

_LOGGER = logging.getLogger(__name__)


class ProfiluxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the controller once per interval and shares the snapshot."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.entry = entry
        self._host = entry.data[CONF_HOST]
        self._username = entry.data.get(CONF_USERNAME, "")
        self._password = entry.data.get(CONF_PASSWORD, "")
        self._interface = entry.data.get(CONF_INTERFACE, INTERFACE_HTTP)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hass.async_add_executor_job(
                fetch_all, self._host, self._username, self._password, self._interface
            )
        except ProfiluxError as err:
            raise UpdateFailed(str(err)) from err
