"""Réglages numériques du mode Manuel."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import HiBatteryEntity
from .hub import HiBatteryHub

NUMBERS = (
    # attribut du hub, clé, nom, min, max, pas, unité, mode, icône, catégorie
    ("manual_setpoint", "manual_setpoint", "Consigne de décharge", 0, 2500, 10,
     UnitOfPower.WATT, NumberMode.SLIDER, "mdi:battery-arrow-down", None),
    ("discharge_max", "discharge_max", "Décharge max", 0, 2500, 50,
     UnitOfPower.WATT, NumberMode.BOX, "mdi:speedometer", EntityCategory.CONFIG),
    ("soc_min", "soc_min", "SOC mini", 0, 100, 1,
     PERCENTAGE, NumberMode.BOX, "mdi:battery-low", EntityCategory.CONFIG),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: HiBatteryHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(HiNumber(hub, *n) for n in NUMBERS)


class HiNumber(HiBatteryEntity, NumberEntity):
    def __init__(self, hub, attr, key, name, low, high, step, unit, mode, icon, category) -> None:
        super().__init__(hub, key, name)
        self._hub_attr = attr
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_mode = mode
        self._attr_icon = icon
        self._attr_entity_category = category

    @property
    def native_value(self) -> float:
        return getattr(self.hub, self._hub_attr)

    async def async_set_native_value(self, value: float) -> None:
        await self.hub.async_set_number(self._hub_attr, value)
