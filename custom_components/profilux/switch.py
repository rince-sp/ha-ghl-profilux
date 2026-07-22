"""Switch platform — manual on/off control of ProfiLux power sockets.

Opt-in (``CONF_CONTROL_SOCKETS``): enabling it exposes one switch per physical
socket. Turning a switch on/off writes the socket's **Function** to "always on" /
"always off" on the controller — a persistent override, the same one the GHL app
offers. The socket's automatic Function is remembered so ``async_set_socket_auto``
can hand control back; that's surfaced here as a ``restore_auto`` when available.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_CONTROL_SOCKETS, DEFAULT_CONTROL_SOCKETS, DOMAIN, MAINS_VOLTAGE
from .coordinator import ProfiluxCoordinator
from .entity import ProfiluxEntity, async_add_discovered


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a switch per socket — only when socket control is enabled."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not entry.options.get(CONF_CONTROL_SOCKETS, DEFAULT_CONTROL_SOCKETS):
        return

    def _builder(data: dict[str, Any]):
        for socket in data.get("sockets", []):
            # Only physical sockets (those the state register answers) are
            # controllable; virtual/expansion channels have no Function to force.
            if socket.get("function") is None:
                continue
            yield ("socket_switch", socket["index"]), (
                lambda i=socket["index"]: ProfiluxSocketSwitch(coordinator, i)
            )

    async_add_discovered(coordinator, entry, async_add_entities, _builder)


class ProfiluxSocketSwitch(ProfiluxEntity, SwitchEntity):
    """Manual on/off override for one ProfiLux socket."""

    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: ProfiluxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_socket_{index}_switch"

    @property
    def name(self) -> str | None:
        data = self._socket_data or {}
        return data.get("name") or f"Socket {self._index + 1}"

    @property
    def _socket_data(self) -> dict[str, Any] | None:
        for socket in (self.coordinator.data or {}).get("sockets", []):
            if socket["index"] == self._index:
                return socket
        return None

    @property
    def is_on(self) -> bool | None:
        data = self._socket_data
        return None if data is None else data.get("is_on")

    @property
    def icon(self) -> str:
        return "mdi:power-socket-de"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._socket_data or {}
        current = data.get("current")
        return {
            # "auto" = following the controller's automation; "on"/"off" = forced.
            "mode": data.get("mode"),
            "can_restore_auto": self.coordinator.auto_function(self._index) is not None,
            # Power info for this outlet, shown alongside the toggle in more-info.
            "current_a": current,
            "power_w": None if current is None else round(current * MAINS_VOLTAGE),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_socket(self._index, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_socket(self._index, False)

    @property
    def available(self) -> bool:
        return super().available and self._socket_data is not None
