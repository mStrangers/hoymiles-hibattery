"""Entité de base."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .hub import HiBatteryHub


class HiBatteryEntity(Entity):
    """Entité rattachée à la batterie, mise à jour par le hub."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: HiBatteryHub, key: str, name: str) -> None:
        self.hub = hub
        self._attr_name = name
        self._attr_unique_id = f"{hub.device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.device_id)},
            name=hub.device_id,
            manufacturer="Hoymiles",
            model=hub.model,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.hub.signal, self._handle_hub_update)
        )

    @callback
    def _handle_hub_update(self) -> None:
        self.async_write_ha_state()
