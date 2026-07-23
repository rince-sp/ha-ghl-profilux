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
from .entity import ProfiluxEntity, async_add_discovered


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create socket/level/alarm binary sensors, and more as they appear."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _builder(data: dict[str, Any]):
        for socket in data.get("sockets", []):
            yield ("socket", socket["index"]), (
                lambda i=socket["index"]: ProfiluxSocket(coordinator, i)
            )
        for level in data.get("levels", []):
            yield ("level_alarm", level["index"]), (
                lambda i=level["index"]: ProfiluxLevelAlarm(coordinator, i)
            )
            # One float-switch sensor per assigned level sensor (min / max).
            for sensor in level.get("sensors", []):
                yield ("level_float", level["index"], sensor["role"]), (
                    lambda i=level["index"], r=sensor["role"]: ProfiluxLevelFloat(coordinator, i, r)
                )
        if data.get("alarm") is not None:
            yield ("alarm",), (lambda: ProfiluxAlarm(coordinator))

    async_add_discovered(coordinator, entry, async_add_entities, _builder)


class ProfiluxSocket(ProfiluxEntity, BinarySensorEntity):
    """Read-only on/off status of one ProfiLux power socket."""

    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(self, coordinator: ProfiluxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_socket_{index}"

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
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._socket_data or {}
        return {"current_a": data.get("current")}

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

    @property
    def name(self) -> str | None:
        data = self._level_data or {}
        return f"{data.get('name') or f'Level {self._index + 1}'} alarm"

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


class ProfiluxLevelFloat(ProfiluxEntity, BinarySensorEntity):
    """One float switch assigned to a level loop (min or max), wet/dry."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator: ProfiluxCoordinator, index: int, role: str) -> None:
        super().__init__(coordinator)
        self._index = index
        self._role = role
        self._attr_unique_id = f"{coordinator.entry.entry_id}_level_{index}_float_{role}"
        self._attr_icon = "mdi:arrow-down-bold" if role == "min" else "mdi:arrow-up-bold"

    @property
    def _sensor(self) -> dict[str, Any] | None:
        for level in (self.coordinator.data or {}).get("levels", []):
            if level["index"] == self._index:
                for sensor in level.get("sensors", []):
                    if sensor["role"] == self._role:
                        return {"level_name": level.get("name"), **sensor}
        return None

    @property
    def name(self) -> str | None:
        data = self._sensor or {}
        base = data.get("level_name") or f"Level {self._index + 1}"
        label = "min" if self._role == "min" else "max"
        return f"{base} {label} float"

    @property
    def is_on(self) -> bool | None:
        # `triggered` is None on firmware that doesn't expose per-float state
        # over the local protocol, so the entity reads "unknown" rather than a
        # fabricated wet/dry. The assigned sensor number is still reported.
        data = self._sensor
        return None if data is None else data.get("triggered")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._sensor or {}
        attrs: dict[str, Any] = {"sensor_number": data.get("number")}
        if data.get("triggered") is None:
            attrs["live_state"] = "not reported by this controller firmware"
        return attrs

    @property
    def available(self) -> bool:
        return super().available and self._sensor is not None


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
