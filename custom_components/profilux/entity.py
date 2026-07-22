"""Shared base entity for ProfiLux."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ProfiluxCoordinator


def async_add_discovered(
    coordinator: ProfiluxCoordinator,
    entry: ConfigEntry,
    async_add_entities: Callable[[list[Entity]], None],
    builder: Callable[[dict[str, Any]], Iterable[tuple[Any, Callable[[], Entity]]]],
) -> None:
    """Add entities as their items appear, now and on every later refresh.

    ``builder(data)`` yields ``(unique_key, factory)`` pairs for the items
    currently present; ``factory`` is only called for keys not yet added. This
    lets a pump/socket/sensor that is activated on the controller after startup
    show up on the next poll without reloading the integration.
    """
    known: set[Any] = set()

    @callback
    def _discover() -> None:
        new: list[Entity] = []
        for key, factory in builder(coordinator.data or {}):
            if key not in known:
                known.add(key)
                new.append(factory())
        if new:
            async_add_entities(new)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


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
