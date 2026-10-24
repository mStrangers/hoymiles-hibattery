"""Sélecteurs : mode de pilotage et planning."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODES
from .entity import HiBatteryEntity
from .hub import HiBatteryHub
from .plans import PlanError


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: HiBatteryHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ModeSelect(hub), PlanSelect(hub)])


class ModeSelect(HiBatteryEntity, SelectEntity):
    _attr_icon = "mdi:home-battery"
    _attr_options = MODES

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "mode", "Mode batterie")

    @property
    def current_option(self) -> str:
        return self.hub.mode

    async def async_select_option(self, option: str) -> None:
        await self.hub.async_set_mode(option)


class PlanSelect(HiBatteryEntity, SelectEntity):
    _attr_icon = "mdi:calendar-multiple"

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "plan", "Planning TOU choisi")

    @property
    def options(self) -> list[str]:
        return list(self.hub.plans)

    @property
    def current_option(self) -> str | None:
        return self.hub.selected_plan

    async def async_select_option(self, option: str) -> None:
        try:
            sent = await self.hub.async_select_plan(option)
        except PlanError as err:
            raise ServiceValidationError(str(err)) from err
        if sent is False:
            raise HomeAssistantError(
                f"Planning choisi, mais pas envoyé à la batterie : {self.hub.last_plan_result}"
            )
