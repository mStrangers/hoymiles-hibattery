"""Configuration via l'interface."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CAPACITY,
    CONF_DEVICE_ID,
    CONF_GRID_EXPORT,
    CONF_GRID_IMPORT,
    CONF_MODEL,
    CONF_PREFIX,
    DEFAULT_CAPACITY,
    DEFAULT_DEVICE_ID,
    DEFAULT_MODEL,
    DEFAULT_PREFIX,
    DOMAIN,
)

ENERGY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="energy")
)
CAPACITY_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0.5, max=20, step=0.01, unit_of_measurement="kWh",
                                  mode=selector.NumberSelectorMode.BOX)
)


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    schema: dict[Any, Any] = {
        vol.Required(CONF_CAPACITY, default=defaults.get(CONF_CAPACITY, DEFAULT_CAPACITY)):
            CAPACITY_SELECTOR,
    }
    for key in (CONF_GRID_IMPORT, CONF_GRID_EXPORT):
        # suggested_value (et non default) : sinon impossible de vider le champ
        schema[vol.Optional(key, description={"suggested_value": defaults.get(key)})] = (
            ENERGY_SELECTOR
        )
    return vol.Schema(schema)


class HiBatteryConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip()
            if not device_id:
                errors[CONF_DEVICE_ID] = "invalid_device"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                user_input[CONF_DEVICE_ID] = device_id
                return self.async_create_entry(title=f"HiBattery {device_id}", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID): str,
                vol.Required(CONF_PREFIX, default=DEFAULT_PREFIX): str,
                vol.Required(CONF_MODEL, default=DEFAULT_MODEL): str,
            }
        ).extend(_options_schema({}).schema)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HiBatteryOptionsFlow()


class HiBatteryOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_options_schema(current))
