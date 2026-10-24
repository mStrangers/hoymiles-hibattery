"""Capteurs binaires."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import HiBatteryEntity
from .hub import HiBatteryHub, _truthy


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: HiBatteryHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PlanIssue(hub), Heating(hub)])


class PlanIssue(HiBatteryEntity, BinarySensorEntity):
    """ON si la batterie ne suit pas le planning local (écart > 3 min)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:calendar-alert"

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "plan_issue", "Planning TOU non respecté")

    @property
    def is_on(self) -> bool:
        return self.hub.plan_issue

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"ecart_instantane": self.hub.plan_deviation()}


class Heating(HiBatteryEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "heat", "Chauffage batterie")

    @property
    def is_on(self) -> bool | None:
        return _truthy(self.hub.quick.get("heat"))
