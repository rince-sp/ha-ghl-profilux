"""Sensor platform — one entity per populated ProfiLux probe."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ProfiluxCoordinator
from .entity import ProfiluxEntity


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
    """Create sensor entities from the first coordinator snapshot."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]
    sensors: list[dict[str, Any]] = (coordinator.data or {}).get("sensors", [])

    type_counts: dict[str, int] = {}
    for sensor in sensors:
        type_counts[sensor["label"]] = type_counts.get(sensor["label"], 0) + 1

    async_add_entities(
        ProfiluxSensor(coordinator, sensor["index"], _unique_suffix(sensor, type_counts))
        for sensor in sensors
    )


class ProfiluxSensor(ProfiluxEntity, SensorEntity):
    """A single ProfiLux probe reading."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ProfiluxCoordinator, index: int, suffix: str) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{suffix}"

        data = self._sensor_data or {}
        self._attr_name = data.get("name") or data.get("label") or f"Sensor {index + 1}"
        self._attr_native_unit_of_measurement = data.get("unit")

        device_class = data.get("device_class")
        if device_class:
            self._attr_device_class = SensorDeviceClass(device_class)

        decimals = data.get("decimals")
        if isinstance(decimals, int):
            self._attr_suggested_display_precision = decimals

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
