"""Config flow for the ProfiLux integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback

from .const import (
    CONF_API_CONTROL,
    CONF_CONTROL_SOCKETS,
    CONF_INTERFACE,
    CONF_SENSOR_TYPES,
    DEFAULT_API_CONTROL,
    DEFAULT_CONTROL_SOCKETS,
    DOMAIN,
    SENSOR_TYPE_AUTO,
    SENSOR_TYPE_CHOICES,
)
from .protocol import (
    INTERFACE_HTTP,
    INTERFACES,
    ProfiluxError,
    test_connection,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_USERNAME, default="admin"): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
        vol.Required(CONF_INTERFACE, default=INTERFACE_HTTP): vol.In(INTERFACES),
    }
)


class ProfiluxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ProfiLux."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "ProfiluxOptionsFlow":
        return ProfiluxOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    test_connection,
                    user_input[CONF_HOST],
                    user_input.get(CONF_USERNAME, ""),
                    user_input.get(CONF_PASSWORD, ""),
                    user_input[CONF_INTERFACE],
                )
            except ProfiluxError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="ProfiLux", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )


class ProfiluxOptionsFlow(config_entries.OptionsFlow):
    """Options: control opt-ins, plus per-sensor type overrides (GHL API mode).

    * ``control_sockets`` — socket on/off (SWMBus interfaces).
    * ``api_control`` — GHL API control (setpoints, feed pause, water change,
      maintenance, lighting); needs the API set to full access.
    * one select per discovered sensor (API mode) — pin its type, since the API
      doesn't report the type. "auto" leaves it to name + unit-probe + heuristic.
    """

    def _sensor_names(self) -> list[str]:
        """Named sensors from the last poll, on any interface.

        A type override helps whenever classification is uncertain: the GHL API
        never reports a sensor's type, and on SWMBus some firmwares report an
        unreliable type register (a pH probe named by location then reads a
        power of ten too high). So the dropdowns are offered for every interface.
        """
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        data = getattr(coordinator, "data", None) or {}
        return [s["name"] for s in data.get("sensors", []) if s.get("name")]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        names = self._sensor_names()

        if user_input is not None:
            data: dict[str, Any] = {
                CONF_CONTROL_SOCKETS: user_input.get(
                    CONF_CONTROL_SOCKETS, DEFAULT_CONTROL_SOCKETS
                ),
                CONF_API_CONTROL: user_input.get(CONF_API_CONTROL, DEFAULT_API_CONTROL),
            }
            overrides = {
                name: user_input[name]
                for name in names
                if user_input.get(name, SENSOR_TYPE_AUTO) != SENSOR_TYPE_AUTO
            }
            if overrides:
                data[CONF_SENSOR_TYPES] = overrides
            return self.async_create_entry(title="", data=data)

        options = self.config_entry.options
        current_types = options.get(CONF_SENSOR_TYPES, {})
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_CONTROL_SOCKETS,
                default=options.get(CONF_CONTROL_SOCKETS, DEFAULT_CONTROL_SOCKETS),
            ): bool,
            vol.Required(
                CONF_API_CONTROL,
                default=options.get(CONF_API_CONTROL, DEFAULT_API_CONTROL),
            ): bool,
        }
        # One type-override dropdown per named sensor (the key is the sensor name,
        # so it shows as the field label).
        for name in names:
            schema[
                vol.Optional(name, default=current_types.get(name, SENSOR_TYPE_AUTO))
            ] = vol.In(SENSOR_TYPE_CHOICES)

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
