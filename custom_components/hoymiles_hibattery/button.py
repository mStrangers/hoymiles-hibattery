"""Boutons."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import HiBatteryEntity
from .hub import HiBatteryHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: HiBatteryHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SendPlan(hub), ReadPlan(hub), Reboot(hub)])


class SendPlan(HiBatteryEntity, ButtonEntity):
    _attr_icon = "mdi:calendar-arrow-right"

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "send_plan", "Envoyer le planning")

    async def async_press(self) -> None:
        await self.hub.async_send_plan(notify=True)


class ReadPlan(HiBatteryEntity, ButtonEntity):
    _attr_icon = "mdi:calendar-search"

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "read_plan", "Lire le planning")

    async def async_press(self) -> None:
        await self.hub.async_read_plan("Mon", notify=True)


class Reboot(HiBatteryEntity, ButtonEntity):
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "reboot", "Redémarrer")

    async def async_press(self) -> None:
        await self.hub.async_reboot()
