"""DataUpdateCoordinator for the ProfiLux integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_API_CONTROL,
    CONF_INTERFACE,
    CONF_PORT,
    CONF_SENSOR_TYPES,
    DEFAULT_API_CONTROL,
    DEFAULT_API_PORT,
    DOMAIN,
    SCAN_INTERVAL,
)
from .protocol import (
    INTERFACE_API,
    INTERFACE_HTTP,
    SOCKET_FUNCTION_ALWAYS_OFF,
    SOCKET_FUNCTION_ALWAYS_ON,
    Controller,
    ProfiluxError,
    api_set,
    fetch_all,
    make_transport,
)

_LOGGER = logging.getLogger(__name__)

# Re-read all GHL API names every N polls (names change rarely; this cuts most
# of the per-poll traffic while still catching renames within a few minutes).
NAME_REFRESH_EVERY = 20


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
        self._port = entry.data.get(CONF_PORT, DEFAULT_API_PORT)
        self._api_control = entry.options.get(CONF_API_CONTROL, DEFAULT_API_CONTROL)
        self._sensor_overrides = dict(entry.options.get(CONF_SENSOR_TYPES, {}))
        # Remembered "automatic" Function per socket, so control can be handed
        # back to the controller after a manual on/off override.
        self._auto_functions: dict[int, int] = {}
        # GHL API name cache: names rarely change, so read them fully only every
        # NAME_REFRESH_EVERY polls (and for any newly-appeared resource); values
        # are always read, so new hardware still shows up on the next poll.
        self._name_cache: dict[str, Any] = {}
        self._poll = 0

    @property
    def supports_socket_control(self) -> bool:
        """Socket on/off is a raw-SWMBus feature; the GHL API can't switch."""
        return self._interface != INTERFACE_API

    @property
    def supports_api_control(self) -> bool:
        """API control entities (setpoints, actions, lighting) — opt-in, API only."""
        return self._interface == INTERFACE_API and self._api_control

    async def _async_update_data(self) -> dict[str, Any]:
        refresh_names = (self._poll % NAME_REFRESH_EVERY) == 0
        self._poll += 1
        try:
            data = await self.hass.async_add_executor_job(
                fetch_all,
                self._host,
                self._username,
                self._password,
                self._interface,
                True,
                self._port,
                self._api_control,
                self._name_cache,
                refresh_names,
                self._sensor_overrides,
            )
        except ProfiluxError as err:
            raise UpdateFailed(str(err)) from err
        # Snapshot each socket's automatic Function while it isn't overridden, so
        # we can restore it later.
        for socket in data.get("sockets", []):
            if socket.get("mode") == "auto" and socket.get("function") is not None:
                self._auto_functions[socket["index"]] = socket["function"]
        return data

    def auto_function(self, index: int) -> int | None:
        """The remembered automatic Function for a socket, if seen un-overridden."""
        return self._auto_functions.get(index)

    async def async_set_socket_function(self, index: int, value: int) -> bool:
        """Write a socket's Function, then refresh so entities reflect it."""

        def _write() -> bool:
            with make_transport(
                self._interface, self._host, self._username, self._password
            ) as transport:
                return Controller(transport).set_socket_function(index, value)

        try:
            ok = await self.hass.async_add_executor_job(_write)
        except ProfiluxError as err:
            raise UpdateFailed(str(err)) from err
        await self.async_request_refresh()
        return ok

    async def async_set_socket(self, index: int, on: bool) -> bool:
        """Force a socket on/off via its "always on" / "always off" Function."""
        value = SOCKET_FUNCTION_ALWAYS_ON if on else SOCKET_FUNCTION_ALWAYS_OFF
        return await self.async_set_socket_function(index, value)

    async def async_set_socket_auto(self, index: int) -> bool:
        """Hand a socket back to automatic control (its remembered Function)."""
        value = self._auto_functions.get(index)
        if value is None:
            return False
        return await self.async_set_socket_function(index, value)

    async def async_api_command(self, command: str, refresh: bool = True) -> bool:
        """Send one GHL API SET command (control action / setpoint), then refresh."""

        def _do() -> bool:
            return api_set(self._host, command)

        try:
            ok = await self.hass.async_add_executor_job(_do)
        except ProfiluxError as err:
            raise HomeAssistantError(str(err)) from err
        if refresh:
            await self.async_request_refresh()
        return ok
