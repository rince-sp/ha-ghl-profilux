"""Button platform — one-shot GHL API actions (opt-in control).

Currently exposes "start a measurement" for the KH Director and ION Director,
when present. Only created when API control is enabled.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
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
    """Create action buttons — only with API control enabled."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.supports_api_control:
        return

    def _builder(data: dict[str, Any]):
        sensors = data.get("sensors", [])
        if any(s.get("index") == "kh" for s in sensors):
            yield ("button", "kh_measure"), (
                lambda: ProfiluxActionButton(
                    coordinator, "kh_measure", "KH Director measure",
                    "SET KHDIRECTOR STARTACTION 1", "mdi:test-tube",
                )
            )
        if any(str(s.get("index", "")).startswith("ion") for s in sensors):
            yield ("button", "ion_measure"), (
                lambda: ProfiluxActionButton(
                    coordinator, "ion_measure", "ION Director measure",
                    "SET IONDIRECTOR[0] STARTACTION 1", "mdi:test-tube",
                )
            )
        # Thunderstorm — a 5-minute storm on demand (available where lighting is).
        if data.get("master_brightness") is not None:
            yield ("button", "thunderstorm"), (
                lambda: ProfiluxActionButton(
                    coordinator, "thunderstorm", "Thunderstorm (5 min)",
                    "SET SPECIALFUNCTION THUNDERSTORM 5", "mdi:weather-lightning",
                )
            )

    async_add_discovered(coordinator, entry, async_add_entities, _builder)


class ProfiluxActionButton(ProfiluxEntity, ButtonEntity):
    """A button that fires one GHL API SET command."""

    def __init__(
        self, coordinator: ProfiluxCoordinator, key: str, name: str, command: str, icon: str
    ) -> None:
        super().__init__(coordinator)
        self._command = command
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"

    async def async_press(self) -> None:
        # No refresh: the action starts a process; the values update on the next
        # normal poll.
        await self.coordinator.async_api_command(self._command, refresh=False)
