"""Capteurs de la batterie HiBattery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_GRID_EXPORT, CONF_GRID_IMPORT, DOMAIN, SLOT_MODE_NAMES
from .entity import HiBatteryEntity
from .hub import HiBatteryHub, _num


def _grid(hub: HiBatteryHub, kind: str, key: str) -> float | None:
    for item in hub.device.get("grid", []) or []:
        if isinstance(item, dict) and item.get("type") == kind:
            return _num(item.get(key))
    return None


@dataclass(frozen=True, kw_only=True)
class HiSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HiBatteryHub], Any]
    attrs_fn: Callable[[HiBatteryHub], dict[str, Any]] | None = None


W = dict(
    native_unit_of_measurement=UnitOfPower.WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
)
WH = dict(
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
)

SENSORS: tuple[HiSensorDescription, ...] = (
    # --- état rapide
    HiSensorDescription(
        key="soc", name="SOC",
        native_unit_of_measurement=PERCENTAGE, device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT, value_fn=lambda h: h.q("soc"),
    ),
    HiSensorDescription(key="bat_p", name="Puissance batterie", icon="mdi:battery-charging",
                        value_fn=lambda h: h.q("bat_p"), **W),
    HiSensorDescription(key="pv_p", name="Puissance PV", icon="mdi:solar-power",
                        value_fn=lambda h: h.q("pv_p"), **W),
    HiSensorDescription(key="grid_on_p", name="Puissance réseau (on-grid)",
                        icon="mdi:transmission-tower", value_fn=lambda h: h.q("grid_on_p"), **W),
    HiSensorDescription(key="grid_off_p", name="Hors-réseau puissance", icon="mdi:power-socket-eu",
                        value_fn=lambda h: h.q("grid_off_p"), **W),
    HiSensorDescription(
        key="pv_total_p", name="Puissance PV totale", icon="mdi:solar-power-variant",
        value_fn=lambda h: None if h.pv_total_power is None else round(h.pv_total_power, 1), **W,
    ),
    # compteur réseau vu par la batterie (Shelly Pro 3EM, firmware >= 1.3.3)
    HiSensorDescription(key="sys_grid_p", name="Puissance réseau compteur",
                        icon="mdi:transmission-tower", value_fn=lambda h: h.q("sys_grid_p"), **W),
    HiSensorDescription(key="sys_load_p", name="Puissance consommation maison",
                        icon="mdi:home-lightning-bolt", value_fn=lambda h: h.q("sys_load_p"), **W),
    HiSensorDescription(
        key="bat_sts", name="Statut batterie", icon="mdi:battery-sync",
        value_fn=lambda h: h.quick.get("bat_sts"),
    ),
    HiSensorDescription(
        key="energy_available", name="Énergie disponible", icon="mdi:battery-high",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: h.energy_available,
        attrs_fn=lambda h: {"capacite_kwh": h.capacity_kwh},
    ),
    # --- système
    HiSensorDescription(key="ems_mode", name="Mode EMS actuel", icon="mdi:cog",
                        value_fn=lambda h: h.system.get("ems_mode")),
    HiSensorDescription(key="pv_e", name="Energie PV", value_fn=lambda h: _num(h.system.get("pv_e")), **WH),
    HiSensorDescription(key="chg_e", name="Energie charge", value_fn=lambda h: _num(h.system.get("chg_e")), **WH),
    HiSensorDescription(key="dchg_e", name="Energie decharge", value_fn=lambda h: _num(h.system.get("dchg_e")), **WH),
    HiSensorDescription(key="plug_out_e", name="Energie prise sortie",
                        value_fn=lambda h: _num(h.system.get("plug_out_e")), **WH),
    HiSensorDescription(key="plug_in_e", name="Energie prise entree",
                        value_fn=lambda h: _num(h.system.get("plug_in_e")), **WH),
    HiSensorDescription(key="eps_out_e", name="Energie EPS sortie", icon="mdi:power-socket-eu",
                        value_fn=lambda h: _num(h.system.get("eps_out_e")), **WH),
    HiSensorDescription(key="eps_in_e", name="Energie EPS entree", icon="mdi:solar-power",
                        value_fn=lambda h: _num(h.system.get("eps_in_e")), **WH),
    # --- appareil
    HiSensorDescription(
        key="bat_temp", name="Température batterie",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: _num(h.device.get("bat_temp")),
    ),
    HiSensorDescription(
        key="rssi", name="RSSI WiFi", entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: _num(h.device.get("rssi")),
    ),
    HiSensorDescription(
        key="grid_v", name="Tension réseau", entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: _grid(h, "grid_on", "v"),
    ),
    HiSensorDescription(
        key="grid_f", name="Fréquence réseau", entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: _grid(h, "grid_on", "f"),
    ),
    # --- compteurs totaux (device/state, firmware >= 1.3.3)
    HiSensorDescription(key="plug_in_total", name="Energie prise entree totale",
                        value_fn=lambda h: _grid(h, "grid_on", "etin"), **WH),
    HiSensorDescription(key="plug_out_total", name="Energie prise sortie totale",
                        value_fn=lambda h: _grid(h, "grid_on", "etout"), **WH),
    HiSensorDescription(key="eps_in_total", name="Energie EPS entree totale", icon="mdi:solar-power",
                        value_fn=lambda h: _grid(h, "grid_off", "etin"), **WH),
    HiSensorDescription(key="eps_out_total", name="Energie EPS sortie totale", icon="mdi:power-socket-eu",
                        value_fn=lambda h: _grid(h, "grid_off", "etout"), **WH),
    # --- pilotage
    HiSensorDescription(
        key="setpoint", name="Consigne envoyée", icon="mdi:target", **W,
        value_fn=lambda h: h.last_setpoint,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: HiBatteryHub = hass.data[DOMAIN][entry.entry_id]
    opts = {**entry.data, **entry.options}
    entities: list[SensorEntity] = [HiSensor(hub, d) for d in SENSORS]
    entities.append(PlanningSensor(hub))
    entities.append(PvEnergySensor(hub))

    daily: dict[str, DailyCounter] = {
        "pv": DailyCounter(hub, "pv_today", "Production PV jour", "mdi:solar-power",
                           lambda h: h.pv_energy_total, 1.0),
        "chg": DailyCounter(hub, "charge_today", "Batterie chargé jour", "mdi:battery-arrow-up",
                            lambda h: _num(h.system.get("chg_e")), 0.001),
        "dchg": DailyCounter(hub, "discharge_today", "Batterie déchargé jour",
                             "mdi:battery-arrow-down", lambda h: _num(h.system.get("dchg_e")), 0.001),
        "out": DailyCounter(hub, "out_today", "Batterie sortie jour", "mdi:power-socket-eu",
                            lambda h: _num(h.system.get("plug_out_e")), 0.001),
        "in": DailyCounter(hub, "in_today", "Batterie entrée jour", "mdi:power-plug",
                           lambda h: _num(h.system.get("plug_in_e")), 0.001),
    }
    if opts.get(CONF_GRID_IMPORT):
        daily["imp"] = EntityDailyCounter(hub, "grid_import_today", "Réseau achat jour",
                                          "mdi:transmission-tower-import", opts[CONF_GRID_IMPORT])
    if opts.get(CONF_GRID_EXPORT):
        daily["exp"] = EntityDailyCounter(hub, "grid_export_today", "Réseau injection jour",
                                          "mdi:transmission-tower-export", opts[CONF_GRID_EXPORT])
    entities.extend(daily.values())
    if "imp" in daily:
        entities.append(HomeConsumption(hub, daily))
    async_add_entities(entities)


class HiSensor(HiBatteryEntity, SensorEntity):
    entity_description: HiSensorDescription

    def __init__(self, hub: HiBatteryHub, description: HiSensorDescription) -> None:
        super().__init__(hub, description.key, description.name)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.hub)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.hub) if fn else None


class PlanningSensor(HiBatteryEntity, SensorEntity):
    """Créneau en cours ; ses attributs alimentent la carte d'édition."""

    _attr_icon = "mdi:calendar-clock"
    # attributs volumineux (> 16 Ko possible) : inutiles dans l'historique
    _unrecorded_attributes = frozenset({"plans", "creneaux", "entites"})

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "planning_tou", "Planning TOU")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # le créneau change avec l'heure
        self.async_on_remove(
            async_track_time_change(self.hass, self._minute, second=0)
        )

    @callback
    def _minute(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        slot = self.hub.current_slot()
        if slot is None:
            return "inconnu"
        return f"{slot['debut']}-{slot['fin']} {SLOT_MODE_NAMES.get(slot['mode'], slot['mode'])}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        hub = self.hub
        return {
            "entry_id": hub.entry.entry_id,
            "planning": hub.selected_plan,
            "mode_pilotage": hub.mode,
            "plans": hub.plans,
            "creneaux": hub.selected_slots,
            "creneau_actuel": hub.current_slot(),
            "planning_non_respecte": hub.plan_issue,
            "dernier_envoi": hub.last_plan_sent.isoformat() if hub.last_plan_sent else None,
            "dernier_resultat": hub.last_plan_result,
            "entites": hub.entity_ids,
        }


class PvEnergySensor(HiBatteryEntity, SensorEntity):
    """Énergie PV totale cumulée (intégrée depuis la puissance PV totale)."""

    _attr_icon = "mdi:solar-power"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    def __init__(self, hub: HiBatteryHub) -> None:
        super().__init__(hub, "pv_energy_total", "Production PV totale énergie")

    @property
    def native_value(self) -> float:
        return round(self.hub.pv_energy_total, 4)


ATTR_LAST_SOURCE = "derniere_valeur_source"


class DailyCounter(HiBatteryEntity, RestoreSensor):
    """Compteur remis à zéro à minuit, calculé à partir d'un compteur cumulé."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        hub: HiBatteryHub,
        key: str,
        name: str,
        icon: str,
        source_fn: Callable[[HiBatteryHub], float | None] | None,
        factor: float = 1.0,
    ) -> None:
        super().__init__(hub, key, name)
        self._attr_icon = icon
        self._source_fn = source_fn
        self._factor = factor
        self._value = 0.0
        self._last_source: float | None = None
        self._attr_last_reset = dt_util.start_of_local_day()
        self._listeners: list[Callable[[], None]] = []

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Prévient une autre entité (consommation maison) à chaque mise à jour."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        state = await self.async_get_last_state()
        if last is not None and last.native_value is not None and state is not None:
            restored_reset = state.attributes.get("last_reset")
            reset_dt = dt_util.parse_datetime(restored_reset) if restored_reset else None
            if reset_dt is not None and reset_dt >= dt_util.start_of_local_day():
                self._value = float(last.native_value)
                # reprise depuis la dernière valeur connue : l'énergie accumulée
                # pendant l'arrêt de Home Assistant est comptée
                self._last_source = _num(state.attributes.get(ATTR_LAST_SOURCE))
        self.async_on_remove(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=0)
        )
        self._update_from_source()

    @callback
    def _midnight(self, _now: datetime) -> None:
        self._value = 0.0
        self._attr_last_reset = dt_util.start_of_local_day()
        self.async_write_ha_state()

    def _read_source(self) -> float | None:
        return self._source_fn(self.hub) if self._source_fn else None

    def _add(self, current: float | None) -> None:
        if current is None:
            return
        if current == 0 and self._last_source:
            # valeur nulle passagère : ignorée (sinon la valeur suivante serait comptée deux fois)
            return
        if self._last_source is not None:
            delta = current - self._last_source if current >= self._last_source else current
            if delta > 0:
                self._value += delta * self._factor
        self._last_source = current

    def _update_from_source(self) -> None:
        self._add(self._read_source())

    @callback
    def _handle_hub_update(self) -> None:
        self._update_from_source()
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return round(self._value, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_LAST_SOURCE: self._last_source}


class EntityDailyCounter(DailyCounter):
    """Compteur journalier alimenté par une entité énergie externe (ex. Shelly)."""

    def __init__(self, hub: HiBatteryHub, key: str, name: str, icon: str, source: str) -> None:
        super().__init__(hub, key, name, icon, None)
        self._source_entity = source

    def _read_source(self) -> float | None:
        state = self.hass.states.get(self._source_entity)
        if state is None:
            return None
        value = _num(state.state)
        if value is None:
            return None
        unit = state.attributes.get("unit_of_measurement")
        if unit == UnitOfEnergy.WATT_HOUR:
            return value / 1000
        if unit == UnitOfEnergy.MEGA_WATT_HOUR:
            return value * 1000
        return value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._source_entity], self._source_changed)
        )

    @callback
    def _source_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._update_from_source()
        self.async_write_ha_state()
        for update in self._listeners:
            update()

    @callback
    def _handle_hub_update(self) -> None:
        """Alimenté par l'entité source, pas par la batterie."""

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": self._source_entity, **super().extra_state_attributes}


class HomeConsumption(HiBatteryEntity, SensorEntity):
    """Consommation de la maison du jour = achat - injection + sortie - entrée batterie."""

    _attr_icon = "mdi:home-lightning-bolt"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, hub: HiBatteryHub, daily: dict[str, DailyCounter]) -> None:
        super().__init__(hub, "home_today", "Consommation maison jour")
        self._daily = daily

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for key in ("imp", "exp"):
            if key in self._daily:
                self.async_on_remove(self._daily[key].async_add_listener(self.async_write_ha_state))

    @property
    def last_reset(self) -> datetime:
        return dt_util.start_of_local_day()

    @property
    def native_value(self) -> float:
        d = self._daily
        val = d["imp"].native_value - (d["exp"].native_value if "exp" in d else 0)
        val += d["out"].native_value - d["in"].native_value
        return round(max(val, 0.0), 2)
