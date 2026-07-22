"""Shared base entity for ProfiLux."""
from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ProfiluxCoordinator


class ProfiluxEntity(CoordinatorEntity[ProfiluxCoordinator]):
    """Base entity that ties everything to a single ProfiLux device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ProfiluxCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        host = entry.data[CONF_HOST]
        device = (coordinator.data or {}).get("device", {})
        serial = device.get("serial")

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(serial) if serial else host)},
            manufacturer=MANUFACTURER,
            model=device.get("model") or "ProfiLux",
            name=entry.title or "ProfiLux",
            sw_version=device.get("sw_version"),
            configuration_url=f"http://{host}",
        )
