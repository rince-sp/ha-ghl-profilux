"""Config flow for the ProfiLux integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .const import CONF_INTERFACE, DOMAIN
from .protocol import INTERFACE_HTTP, INTERFACES, ProfiluxError, test_connection

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
