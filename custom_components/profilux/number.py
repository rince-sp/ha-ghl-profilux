"""Number platform — editable setpoints over the GHL API (opt-in control).

Each regulated source that carries a target value (``DESVALUE``) — temperature,
pH, the KH Director, ION Director values — becomes an editable number. Writing it
sends ``SET <resource> DESVALUE <value>`` (stored on the controller, exactly as if
entered in the GHL software). Only created when API control is enabled.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
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
    """Create a setpoint number per regulated source — only with API control on."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.supports_api_control:
        return

    def _builder(data: dict[str, Any]):
        for sp in data.get("setpoints", []):
            yield ("setpoint", sp["key"]), (
                lambda k=sp["key"]: ProfiluxSetpoint(coordinator, k)
            )

    async_add_discovered(coordinator, entry, async_add_entities, _builder)


class ProfiluxSetpoint(ProfiluxEntity, NumberEntity):
    """Editable target value for one regulated source."""

    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:target"

    def __init__(self, coordinator: ProfiluxCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_setpoint_{key}"
        data = self._data or {}
        if (data.get("device_class")) == "temperature":
            self._attr_device_class = NumberDeviceClass.TEMPERATURE

    @property
    def _data(self) -> dict[str, Any] | None:
        for sp in (self.coordinator.data or {}).get("setpoints", []):
            if sp["key"] == self._key:
                return sp
        return None

    @property
    def name(self) -> str | None:
        data = self._data or {}
        return f"{data.get('name') or f'Setpoint {self._key}'} target"

    @property
    def native_value(self) -> float | None:
        data = self._data
        return None if data is None else data.get("value")

    @property
    def native_unit_of_measurement(self) -> str | None:
        return (self._data or {}).get("unit")

    @property
    def native_min_value(self) -> float:
        return (self._data or {}).get("min", 0.0)

    @property
    def native_max_value(self) -> float:
        return (self._data or {}).get("max", 100.0)

    @property
    def native_step(self) -> float:
        return (self._data or {}).get("step", 0.1)

    async def async_set_native_value(self, value: float) -> None:
        data = self._data
        if data is None:
            return
        # Trim trailing zeros so "24.5" isn't sent as "24.500000".
        text = f"{value:g}"
        await self.coordinator.async_api_command(
            f"SET {data['resource']} DESVALUE {text}"
        )

    @property
    def available(self) -> bool:
        return super().available and self._data is not None
