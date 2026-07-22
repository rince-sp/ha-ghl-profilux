"""Sensor platform — one entity per populated ProfiLux probe."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfPower, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAINS_VOLTAGE
from .coordinator import ProfiluxCoordinator
from .entity import ProfiluxEntity, async_add_discovered


def _socket_currents(coordinator: ProfiluxCoordinator) -> list[float]:
    return [
        s["current"]
        for s in (coordinator.data or {}).get("sockets", [])
        if s.get("current") is not None
    ]


def _unique_suffix(sensor: dict[str, Any], type_counts: dict[str, int]) -> str:
    """Stable per-sensor key — type name when unique, else include the index."""
    key = sensor["label"].lower().replace(" ", "_")
    if type_counts.get(sensor["label"], 0) > 1:
        return f"{key}_{sensor['index']}"
    return key


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensor entities, and keep creating them as items appear."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _builder(data: dict[str, Any]):
        sensors: list[dict[str, Any]] = data.get("sensors", [])
        type_counts: dict[str, int] = {}
        for sensor in sensors:
            type_counts[sensor["label"]] = type_counts.get(sensor["label"], 0) + 1
        for sensor in sensors:
            suffix = _unique_suffix(sensor, type_counts)
            yield ("sensor", sensor["index"]), (
                lambda i=sensor["index"], s=suffix: ProfiluxSensor(coordinator, i, s)
            )
        # One current sensor per socket that reports a draw (digital powerbar).
        for socket in data.get("sockets", []):
            if socket.get("current") is not None:
                yield ("current", socket["index"]), (
                    lambda i=socket["index"]: ProfiluxSocketCurrent(coordinator, i)
                )
        # Aggregate current + estimated power across the powerbar.
        if any(s.get("current") is not None for s in data.get("sockets", [])):
            yield ("total_current",), (lambda: ProfiluxTotalCurrent(coordinator))
            yield ("total_power",), (lambda: ProfiluxTotalPower(coordinator))
        # A status text per level control loop.
        for level in data.get("levels", []):
            yield ("level_status", level["index"]), (
                lambda i=level["index"]: ProfiluxLevelStatus(coordinator, i)
            )
        # Remaining reservoir volume per in-use dosing pump.
        for pump in data.get("dosing_pumps", []):
            yield ("dosing", pump["index"]), (
                lambda i=pump["index"]: ProfiluxDosingPump(coordinator, i)
            )

    async_add_discovered(coordinator, entry, async_add_entities, _builder)


class ProfiluxSensor(ProfiluxEntity, SensorEntity):
    """A single ProfiLux probe reading."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ProfiluxCoordinator, index: int, suffix: str) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{suffix}"

        data = self._sensor_data or {}
        self._attr_native_unit_of_measurement = data.get("unit")

        device_class = data.get("device_class")
        if device_class:
            self._attr_device_class = SensorDeviceClass(device_class)

        decimals = data.get("decimals")
        if isinstance(decimals, int):
            self._attr_suggested_display_precision = decimals

    @property
    def name(self) -> str | None:
        data = self._sensor_data or {}
        return data.get("name") or data.get("label") or f"Sensor {self._index + 1}"

    @property
    def _sensor_data(self) -> dict[str, Any] | None:
        for sensor in (self.coordinator.data or {}).get("sensors", []):
            if sensor["index"] == self._index:
                return sensor
        return None

    @property
    def native_value(self) -> float | None:
        data = self._sensor_data
        return None if data is None else data.get("value")

    @property
    def available(self) -> bool:
        return super().available and self._sensor_data is not None


class ProfiluxSocketCurrent(ProfiluxEntity, SensorEntity):
    """Current drawn by one socket (digital powerbar), in amps."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: ProfiluxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_socket_{index}_current"

    @property
    def name(self) -> str | None:
        data = self._socket_data or {}
        return f"{data.get('name') or f'Socket {self._index + 1}'} current"

    @property
    def _socket_data(self) -> dict[str, Any] | None:
        for socket in (self.coordinator.data or {}).get("sockets", []):
            if socket["index"] == self._index:
                return socket
        return None

    @property
    def native_value(self) -> float | None:
        data = self._socket_data
        return None if data is None else data.get("current")

    @property
    def available(self) -> bool:
        return super().available and self._socket_data is not None


class ProfiluxTotalCurrent(ProfiluxEntity, SensorEntity):
    """Total current across all powerbar sockets, in amps."""

    _attr_name = "Total current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: ProfiluxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_total_current"

    @property
    def native_value(self) -> float | None:
        currents = _socket_currents(self.coordinator)
        return round(sum(currents), 2) if currents else None


class ProfiluxTotalPower(ProfiluxEntity, SensorEntity):
    """Estimated total power draw (total current × mains voltage), in watts."""

    _attr_name = "Total power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: ProfiluxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_total_power"

    @property
    def native_value(self) -> float | None:
        currents = _socket_currents(self.coordinator)
        return round(sum(currents) * MAINS_VOLTAGE) if currents else None


class ProfiluxDosingPump(ProfiluxEntity, SensorEntity):
    """Remaining reservoir volume of one dosing pump ("Dosierpumpe"), in mL."""

    _attr_device_class = SensorDeviceClass.VOLUME_STORAGE
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cup-water"

    def __init__(self, coordinator: ProfiluxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dosing_{index}_fill"

    @property
    def name(self) -> str | None:
        data = self._pump_data or {}
        return f"{data.get('name') or f'Dosing pump {self._index + 1}'} fill level"

    @property
    def _pump_data(self) -> dict[str, Any] | None:
        for pump in (self.coordinator.data or {}).get("dosing_pumps", []):
            if pump["index"] == self._index:
                return pump
        return None

    @property
    def native_value(self) -> float | None:
        data = self._pump_data
        return None if data is None else data.get("fill_ml")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._pump_data or {}
        return {
            "capacity_ml": data.get("capacity_ml"),
            "percent": data.get("percent"),
            "mode": data.get("mode"),
        }

    @property
    def available(self) -> bool:
        return super().available and self._pump_data is not None


class ProfiluxLevelStatus(ProfiluxEntity, SensorEntity):
    """Overall status of one level ("Niveau") control loop."""

    _attr_icon = "mdi:water-check"

    def __init__(self, coordinator: ProfiluxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_level_{index}_status"

    @property
    def name(self) -> str | None:
        data = self._level_data or {}
        return f"{data.get('name') or f'Level {self._index + 1}'} status"

    @property
    def _level_data(self) -> dict[str, Any] | None:
        for level in (self.coordinator.data or {}).get("levels", []):
            if level["index"] == self._index:
                return level
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._level_data or {}
        return {
            "active": data.get("active"),
            "sensors": [
                {"role": s["role"], "sensor": s["number"], "triggered": s["triggered"]}
                for s in data.get("sensors", [])
            ],
        }

    @property
    def native_value(self) -> str | None:
        data = self._level_data
        if data is None:
            return None
        if data.get("alarm"):
            return "Alarm"
        if data.get("fill"):
            return "Filling"
        if data.get("drain"):
            return "Draining"
        return "OK"

    @property
    def available(self) -> bool:
        return super().available and self._level_data is not None
