"""Binary sensor platform — power-socket states and the global alarm.

Phase 1 is read-only: sockets are surfaced as ``binary_sensor`` (on/off status)
rather than switches, so nothing here can ever toggle live aquarium equipment.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ProfiluxCoordinator
from .entity import ProfiluxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one binary sensor per socket, plus the alarm indicator."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    entities: list[BinarySensorEntity] = [
        ProfiluxSocket(coordinator, socket["index"]) for socket in data.get("sockets", [])
    ]
    entities += [
        ProfiluxLevelAlarm(coordinator, level["index"]) for level in data.get("levels", [])
    ]
    if data.get("alarm") is not None:
        entities.append(ProfiluxAlarm(coordinator))

    async_add_entities(entities)


class ProfiluxSocket(ProfiluxEntity, BinarySensorEntity):
    """Read-only on/off status of one ProfiLux power socket."""

    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(self, coordinator: ProfiluxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_socket_{index}"
        data = self._socket_data or {}
        self._attr_name = data.get("name") or f"Socket {index + 1}"

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
    def available(self) -> bool:
        return super().available and self._socket_data is not None


class ProfiluxLevelAlarm(ProfiluxEntity, BinarySensorEntity):
    """Alarm state of one level ("Niveau") control loop, with fill/drain attrs."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:water-alert"

    def __init__(self, coordinator: ProfiluxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_level_{index}_alarm"
        data = self._level_data or {}
        name = data.get("name") or f"Level {index + 1}"
        self._attr_name = f"{name} alarm"

    @property
    def _level_data(self) -> dict[str, Any] | None:
        for level in (self.coordinator.data or {}).get("levels", []):
            if level["index"] == self._index:
                return level
        return None

    @property
    def is_on(self) -> bool | None:
        data = self._level_data
        return None if data is None else data.get("alarm")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._level_data or {}
        return {"fill_active": data.get("fill"), "drain_active": data.get("drain")}

    @property
    def available(self) -> bool:
        return super().available and self._level_data is not None


class ProfiluxAlarm(ProfiluxEntity, BinarySensorEntity):
    """Controller alarm state."""

    _attr_name = "Alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: ProfiluxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_alarm"

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("alarm")
