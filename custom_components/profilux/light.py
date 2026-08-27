"""Light platform — illumination master brightness over the GHL API.

The API exposes one live, settable lighting control: the **master brightness**,
which scales all illumination channels together (per-channel live brightness is
read-only, surfaced as sensors). This maps it to a single dimmable light. Only
created when API control is enabled and the controller reports a master value.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
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
    """Create the master-brightness light — only with API control on."""
    coordinator: ProfiluxCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.supports_api_control:
        return

    def _builder(data: dict[str, Any]):
        if data.get("master_brightness") is not None:
            yield ("master_light",), (lambda: ProfiluxMasterLight(coordinator))

    async_add_discovered(coordinator, entry, async_add_entities, _builder)


def _pct_to_255(pct: float) -> int:
    return max(0, min(255, round(pct / 100 * 255)))


def _255_to_pct(value: int) -> int:
    return max(0, min(100, round(value / 255 * 100)))


class ProfiluxMasterLight(ProfiluxEntity, LightEntity):
    """Illumination master brightness as a dimmable light."""

    _attr_name = "Illumination"
    _attr_icon = "mdi:brightness-6"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: ProfiluxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_master_light"

    @property
    def _pct(self) -> float | None:
        return (self.coordinator.data or {}).get("master_brightness")

    @property
    def is_on(self) -> bool | None:
        pct = self._pct
        return None if pct is None else pct > 0

    @property
    def brightness(self) -> int | None:
        pct = self._pct
        return None if pct is None else _pct_to_255(pct)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            pct = _255_to_pct(kwargs[ATTR_BRIGHTNESS])
        else:
            # No level given → full unless it's already on at some level.
            current = self._pct
            pct = int(current) if current else 100
        await self.coordinator.async_api_command(
            f"SET ILLUMINATION MASTERBRIGHTNESS {pct}"
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_api_command("SET ILLUMINATION MASTERBRIGHTNESS 0")

    @property
    def available(self) -> bool:
        return super().available and self._pct is not None
