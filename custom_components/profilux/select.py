"""Select platform — per-socket Auto / On / Off control.

Opt-in (``CONF_CONTROL_SOCKETS``, the same option that enables the switches).
Each controllable socket gets a select with three modes:

* **On**  – force the socket on  (Function → "always on")
* **Off** – force the socket off (Function → "always off")
* **Auto** – hand control back to the controller (restore the socket's original
  Function, remembered while it wasn't overridden)

"Auto" is only offered once the integration has seen the socket un-overridden
since startup — otherwise its automatic Function isn't known and the option is
skipped.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_CONTROL_SOCKETS, DEFAULT_CONTROL_SOCKETS, DOMAIN
from .coordinator import ProfiluxCoordinator
from .entity import ProfiluxEntity, async_add_discovered

MODE_AUTO = "Auto"
MODE_ON = "On"
MODE_OFF = "Off"
_MODE_LABELS = {"auto": MODE_AUTO, "on": MODE_ON, "off": MODE_OFF}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create an Auto/On/Off select per socket — only when control is enabled."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not entry.options.get(CONF_CONTROL_SOCKETS, DEFAULT_CONTROL_SOCKETS):
        return

    def _builder(data: dict[str, Any]):
        for socket in data.get("sockets", []):
            if socket.get("function") is None:
                continue
            yield ("socket_mode", socket["index"]), (
                lambda i=socket["index"]: ProfiluxSocketMode(coordinator, i)
            )

    async_add_discovered(coordinator, entry, async_add_entities, _builder)


class ProfiluxSocketMode(ProfiluxEntity, SelectEntity):
    """Auto / On / Off control for one ProfiLux socket."""

    _attr_icon = "mdi:power-socket-de"

    def __init__(self, coordinator: ProfiluxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_socket_{index}_mode"

    @property
    def name(self) -> str | None:
        data = self._socket_data or {}
        return f"{data.get('name') or f'Socket {self._index + 1}'} mode"

    @property
    def _socket_data(self) -> dict[str, Any] | None:
        for socket in (self.coordinator.data or {}).get("sockets", []):
            if socket["index"] == self._index:
                return socket
        return None

    @property
    def options(self) -> list[str]:
        # Offer "Auto" only when the socket's automatic Function is known.
        if self.coordinator.auto_function(self._index) is not None:
            return [MODE_AUTO, MODE_ON, MODE_OFF]
        return [MODE_ON, MODE_OFF]

    @property
    def current_option(self) -> str | None:
        data = self._socket_data
        if data is None:
            return None
        return _MODE_LABELS.get(data.get("mode"))

    async def async_select_option(self, option: str) -> None:
        if option == MODE_ON:
            await self.coordinator.async_set_socket(self._index, True)
        elif option == MODE_OFF:
            await self.coordinator.async_set_socket(self._index, False)
        elif option == MODE_AUTO:
            await self.coordinator.async_set_socket_auto(self._index)

    @property
    def available(self) -> bool:
        return super().available and self._socket_data is not None
