"""Cœur de l'intégration : MQTT, pilotage, plannings et surveillance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import logging
from typing import Any

from homeassistant.components import mqtt, persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACK_TIMEOUT_S,
    CONF_CAPACITY,
    CONF_DEVICE_ID,
    CONF_MODEL,
    CONF_PREFIX,
    DEFAULT_CAPACITY,
    DEFAULT_MODEL,
    DEFAULT_PREFIX,
    DOMAIN,
    MANUAL_INTERVAL_S,
    MODE_MANUAL,
    MODE_PLANNING,
    MODE_TO_EMS,
    MODES,
    MONITOR_DELAY_S,
    MONITOR_INTERVAL_S,
    MONITOR_RETRY_S,
    QUICK_STALE_S,
    SIGNAL_UPDATE,
    SLOT_FORCED_CHARGE,
    SLOT_PV_CHARGE,
    PV_SAVE_INTERVAL_S,
    WEEK_DAYS,
)
from .plans import (
    MAX_PLANS,
    PlanError,
    current_slot,
    default_plans,
    describe_slot,
    from_day_plan,
    to_day_plan,
    validate_name,
    validate_slots,
)

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool | None:
    """Booléen envoyé par la batterie (true/false, 0/1, "0"/"1"...)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def _ack_ok(ack: dict | None) -> bool:
    """Réponse de la batterie avec status 0 (succès)."""
    if ack is None:
        return False
    try:
        return int(ack.get("status", 1)) == 0
    except (TypeError, ValueError):
        return False


class HiBatteryHub:
    """Une batterie HiBattery pilotée en MQTT local."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.device_id: str = entry.data[CONF_DEVICE_ID]
        self.prefix: str = entry.data.get(CONF_PREFIX, DEFAULT_PREFIX)
        self.model: str = entry.data.get(CONF_MODEL, DEFAULT_MODEL)
        self.signal = SIGNAL_UPDATE.format(entry.entry_id)

        # Dernières données reçues
        self.quick: dict[str, Any] = {}
        self.system: dict[str, Any] = {}
        self.device: dict[str, Any] = {}
        self.quick_time: datetime | None = None

        # Réglages persistants
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.plans: dict[str, list[dict[str, Any]]] = {}
        self.selected_plan: str | None = None
        self.mode: str = MODE_PLANNING
        self.manual_setpoint: float = 0
        self.discharge_max: float = 2500
        self.soc_min: float = 10
        self.pv_energy_total: float = 0.0  # kWh

        # État courant
        self.last_setpoint: float = 0
        self.plan_issue: bool = False
        self.last_plan_sent: datetime | None = None
        self.last_plan_result: str | None = None
        self._bad_since: datetime | None = None
        self._last_resend: datetime | None = None
        self._last_ems_push: datetime | None = None
        self._last_pv_time: datetime | None = None
        self._last_pv_power: float | None = None
        self._last_pv_save: datetime | None = None

        self._futures: dict[str, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()
        self._unsubs: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------ topics
    @property
    def _sensor_base(self) -> str:
        return f"{self.prefix}/sensor/{self.device_id}"

    def topic(self, name: str) -> str:
        return {
            "quick": f"{self._sensor_base}/quick/state",
            "device": f"{self._sensor_base}/device/state",
            "system": f"{self._sensor_base}/system/state",
            "tou_get": f"{self._sensor_base}/tou_plan/get",
            "tou_status": f"{self._sensor_base}/tou_plan/status",
            "day_set": f"{self._sensor_base}/tou_day_plan/set",
            "day_ack": f"{self._sensor_base}/tou_day_plan/ack",
            "week_set": f"{self._sensor_base}/tou_week_plan/set",
            "week_ack": f"{self._sensor_base}/tou_week_plan/ack",
            "ems": f"{self.prefix}/select/{self.device_id}/ems_mode/command",
            "power": f"{self.prefix}/number/{self.device_id}/power_ctrl/set",
            "reboot": f"{self.prefix}/button/{self.device_id}/reboot/trigger",
        }[name]

    @property
    def capacity_kwh(self) -> float:
        opts = {**self.entry.data, **self.entry.options}
        try:
            per_pack = float(opts.get(CONF_CAPACITY, DEFAULT_CAPACITY))
        except (TypeError, ValueError):
            per_pack = DEFAULT_CAPACITY
        packs = int(_num(self.device.get("pack_num")) or 1)
        return per_pack * max(packs, 1)

    # --------------------------------------------------------------- lifecycle
    async def async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self.plans = stored.get("plans") or default_plans()
        self.selected_plan = stored.get("selected_plan")
        if self.selected_plan not in self.plans:
            self.selected_plan = next(iter(self.plans))
        mode = stored.get("mode", MODE_PLANNING)
        self.mode = mode if mode in MODES else MODE_PLANNING
        self.manual_setpoint = float(stored.get("manual_setpoint", 0))
        self.discharge_max = float(stored.get("discharge_max", 2500))
        self.soc_min = float(stored.get("soc_min", 10))
        self.pv_energy_total = float(stored.get("pv_energy_total", 0.0))
        if not stored:
            self._save()

        for name in ("quick", "device", "system", "tou_status", "day_ack", "week_ack"):
            self._unsubs.append(
                await mqtt.async_subscribe(self.hass, self.topic(name), self._on_message, qos=1)
            )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._manual_tick, timedelta(seconds=MANUAL_INTERVAL_S)
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._monitor_tick, timedelta(seconds=MONITOR_INTERVAL_S)
            )
        )

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for fut in self._futures.values():
            if not fut.done():
                fut.cancel()
        await self._store.async_save(self._data_to_store())

    def _data_to_store(self) -> dict[str, Any]:
        return {
            "plans": self.plans,
            "selected_plan": self.selected_plan,
            "mode": self.mode,
            "manual_setpoint": self.manual_setpoint,
            "discharge_max": self.discharge_max,
            "soc_min": self.soc_min,
            "pv_energy_total": round(self.pv_energy_total, 4),
        }

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._data_to_store, 1)

    @callback
    def notify_listeners(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    # -------------------------------------------------------------------- MQTT
    @callback
    def _on_message(self, msg: Any) -> None:
        topic = msg.topic
        try:
            payload = json.loads(msg.payload)
        except (TypeError, ValueError):
            _LOGGER.debug("Message non JSON sur %s : %s", topic, msg.payload)
            return
        if not isinstance(payload, dict):
            return

        fut = self._futures.get(topic)
        if fut is not None and not fut.done():
            fut.set_result(payload)

        if topic == self.topic("quick"):
            self.quick = payload
            now = dt_util.utcnow()
            self.quick_time = now
            self._integrate_pv(now)
        elif topic == self.topic("system"):
            self.system = payload
        elif topic == self.topic("device"):
            self.device = payload
        else:
            return
        self.notify_listeners()

    async def _publish(self, name: str, payload: Any, qos: int = 1) -> None:
        if not isinstance(payload, str):
            payload = json.dumps(payload, separators=(",", ":"))
        await mqtt.async_publish(self.hass, self.topic(name), payload, qos=qos, retain=False)

    async def _request(self, set_name: str, payload: Any, reply_name: str) -> dict | None:
        """Publie et attend la réponse de la batterie (ou None si délai dépassé)."""
        reply_topic = self.topic(reply_name)
        fut: asyncio.Future = self.hass.loop.create_future()
        self._futures[reply_topic] = fut
        try:
            await self._publish(set_name, payload)
            return await asyncio.wait_for(fut, ACK_TIMEOUT_S)
        except TimeoutError:
            return None
        finally:
            if self._futures.get(reply_topic) is fut:
                self._futures.pop(reply_topic)

    # ------------------------------------------------------------- mesures
    def q(self, key: str) -> float | None:
        return _num(self.quick.get(key))

    @property
    def quick_fresh(self) -> bool:
        return self.quick_time is not None and (
            dt_util.utcnow() - self.quick_time
        ).total_seconds() < QUICK_STALE_S

    @property
    def pv_total_power(self) -> float | None:
        pv = self.q("pv_p")
        off = self.q("grid_off_p")
        if pv is None and off is None:
            return None
        return (pv or 0.0) + max(0.0, -(off or 0.0))

    @property
    def energy_available(self) -> float | None:
        soc = self.q("soc")
        if soc is None:
            return None
        return round(soc / 100 * self.capacity_kwh, 2)

    def _integrate_pv(self, now: datetime) -> None:
        """Intègre la puissance PV totale (méthode des rectangles à gauche)."""
        if self._last_pv_time is not None and self._last_pv_power is not None:
            seconds = (now - self._last_pv_time).total_seconds()
            if 0 < seconds <= 300:
                self.pv_energy_total += self._last_pv_power * seconds / 3_600_000
        # Sauvegarde espacée : appeler async_delay_save à chaque message repousserait
        # l'écriture indéfiniment (quick/state est fréquent)
        if (
            self._last_pv_save is None
            or (now - self._last_pv_save).total_seconds() >= PV_SAVE_INTERVAL_S
        ):
            self._last_pv_save = now
            self._save()
        self._last_pv_time = now
        self._last_pv_power = self.pv_total_power

    # ------------------------------------------------------------- pilotage
    async def async_set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise PlanError(f"Mode inconnu : {mode}")
        self.mode = mode
        self._bad_since = None
        self.plan_issue = False
        self._save()
        await self._publish("ems", MODE_TO_EMS[mode])
        self._last_ems_push = dt_util.utcnow()
        self.notify_listeners()
        if mode == MODE_PLANNING:
            self.hass.async_create_task(self.async_send_plan(notify=True))
        elif mode == MODE_MANUAL:
            await self._manual_tick()

    async def async_set_number(self, key: str, value: float) -> None:
        setattr(self, key, float(value))
        self._save()
        self.notify_listeners()
        if key == "manual_setpoint" and self.mode == MODE_MANUAL:
            await self._manual_tick()

    def manual_target(self) -> float:
        target = max(0.0, min(self.manual_setpoint, self.discharge_max))
        soc = self.q("soc")
        if soc is not None and soc <= self.soc_min:
            target = 0.0
        return float(round(target))

    async def _manual_tick(self, _now: datetime | None = None) -> None:
        if self.mode != MODE_MANUAL:
            return
        now = dt_util.utcnow()
        ems = self.system.get("ems_mode")
        if ems and ems != "mqtt_ctrl" and (
            self._last_ems_push is None or (now - self._last_ems_push).total_seconds() > 120
        ):
            await self._publish("ems", "mqtt_ctrl")
            self._last_ems_push = now
        target = self.manual_target()
        await self._publish("power", str(int(target)))
        if target != self.last_setpoint:
            self.last_setpoint = target
            self.notify_listeners()

    async def async_reboot(self) -> None:
        await self._publish("reboot", "RESTART")

    # ------------------------------------------------------------- plannings
    @property
    def selected_slots(self) -> list[dict[str, Any]]:
        return self.plans.get(self.selected_plan or "", [])

    def current_slot(self) -> dict[str, Any] | None:
        now = dt_util.now()
        return current_slot(self.selected_slots, now.hour * 60 + now.minute)

    async def async_select_plan(self, name: str) -> bool | None:
        """Choisit un planning ; renvoie le résultat de l'envoi (None si pas en mode Planning)."""
        if name not in self.plans:
            raise PlanError(f"Planning inconnu : {name}")
        self.selected_plan = name
        self._bad_since = None
        self.plan_issue = False
        self._save()
        self.notify_listeners()
        if self.mode == MODE_PLANNING:
            return await self.async_send_plan(notify=True)
        return None

    async def async_save_plan(
        self, name: str, slots: Any, select: bool = False, old_name: str | None = None
    ) -> tuple[str, bool | None]:
        """Enregistre un planning ; renvoie (nom, résultat de l'envoi ou None si non envoyé)."""
        name = validate_name(name)
        clean = validate_slots(slots)
        if old_name and old_name != name:
            if old_name not in self.plans:
                raise PlanError(f"Planning inconnu : {old_name}")
            if name in self.plans:
                raise PlanError(f"Un planning « {name} » existe déjà")
            # renommage en conservant l'ordre
            self.plans = {(name if k == old_name else k): v for k, v in self.plans.items()}
            if self.selected_plan == old_name:
                self.selected_plan = name
        elif name not in self.plans and len(self.plans) >= MAX_PLANS:
            raise PlanError(f"{MAX_PLANS} plannings maximum")
        self.plans[name] = clean
        self._save()
        self.notify_listeners()
        sent: bool | None = None
        if select and name != self.selected_plan:
            sent = await self.async_select_plan(name)
        elif name == self.selected_plan and self.mode == MODE_PLANNING:
            # le planning actif a changé : on le renvoie à la batterie
            sent = await self.async_send_plan(notify=True)
        return name, sent

    async def async_delete_plan(self, name: str) -> None:
        if name not in self.plans:
            raise PlanError(f"Planning inconnu : {name}")
        if len(self.plans) <= 1:
            raise PlanError("Impossible de supprimer le dernier planning")
        del self.plans[name]
        self._save()
        if self.selected_plan == name:
            await self.async_select_plan(next(iter(self.plans)))
        else:
            self.notify_listeners()

    async def async_send_plan(self, name: str | None = None, notify: bool = True) -> bool:
        name = name or self.selected_plan
        slots = self.plans.get(name or "")
        if not slots:
            self._finish_send(False, f"Planning inconnu : {name}", notify)
            return False
        if self.mode == MODE_PLANNING and name != self.selected_plan:
            # sinon la surveillance remettrait le planning choisi au bout de 3 min
            self.selected_plan = name
            self._save()
        async with self._send_lock:
            ack = await self._request("day_set", to_day_plan(slots), "day_ack")
            if not _ack_ok(ack):
                msg = (
                    "Pas de réponse de la batterie (planning journalier)."
                    if ack is None
                    else f"Planning journalier refusé : {ack.get('err_msg', ack)}"
                )
                self._finish_send(False, msg, notify)
                return False
            ack = await self._request(
                "week_set", {"week_plan": [{"week": WEEK_DAYS, "day_idx": 1}]}, "week_ack"
            )
            if not _ack_ok(ack):
                msg = (
                    "Pas de réponse de la batterie (planning hebdomadaire)."
                    if ack is None
                    else f"Planning hebdomadaire refusé : {ack.get('err_msg', ack)}"
                )
                self._finish_send(False, msg, notify)
                return False
            if self.mode == MODE_PLANNING:
                await self._publish("ems", "tou_plan")
                self._last_ems_push = dt_util.utcnow()
        self.last_plan_sent = dt_util.utcnow()
        self._bad_since = None
        self.plan_issue = False
        self._finish_send(
            True, f"Planning « {name} » enregistré dans la batterie ({len(slots)} créneaux).", notify
        )
        return True

    def _finish_send(self, ok: bool, message: str, notify: bool) -> None:
        self.last_plan_result = message
        if ok:
            _LOGGER.info(message)
        else:
            _LOGGER.warning(message)
        if notify:
            persistent_notification.async_create(
                self.hass,
                message,
                title="Batterie - planning TOU" + ("" if ok else " : échec"),
                notification_id=f"{DOMAIN}_plan",
            )
        self.notify_listeners()

    async def async_read_plan(self, day: str = "Mon", notify: bool = True) -> dict[str, Any]:
        if day not in WEEK_DAYS:
            raise PlanError(f"Jour invalide : {day}")
        # un seul message à la fois vers la batterie
        async with self._send_lock:
            reply = await self._request("tou_get", {"week": day}, "tou_status")
        if reply is None:
            result = {"ok": False, "message": "Pas de réponse de la batterie."}
        elif not _ack_ok(reply):
            result = {"ok": False, "message": f"Erreur : {reply.get('err_msg', reply)}"}
        else:
            slots = from_day_plan(reply.get("day_plan", []))
            result = {"ok": True, "day": day, "slots": slots, "message": "\n".join(
                f"- {describe_slot(s)}" for s in slots
            )}
        if notify:
            persistent_notification.async_create(
                self.hass,
                result["message"],
                title=f"Batterie - planning en mémoire ({day})",
                notification_id=f"{DOMAIN}_read",
            )
        return result

    # ------------------------------------------------------------- surveillance
    def plan_deviation(self) -> bool | None:
        """True si la batterie ne suit pas le créneau local en cours."""
        if self.mode != MODE_PLANNING:
            return False
        if not self.quick_fresh:
            return None
        slot = self.current_slot()
        p, g, soc = self.q("bat_p"), self.q("grid_on_p"), self.q("soc")
        if slot is None or p is None or g is None:
            return None
        if _truthy(self.quick.get("heat")):
            # le chauffage de la batterie prend du réseau : comportement non comparable
            return None
        soc = 50.0 if soc is None else soc
        # appareils branchés sur la prise EPS : alimentés par le réseau via la batterie
        eps_load = max(0.0, self.q("grid_off_p") or 0.0)
        grid_draw = g < -(30 + eps_load)
        if slot["mode"] == SLOT_FORCED_CHARGE:
            pv = self.q("pv_p") or 0.0
            # le PV peut suffire à fournir la puissance de charge demandée
            no_grid_charge = soc < slot["sh"] - 2 and not grid_draw and pv < slot["pc"] - 30
            return no_grid_charge or g < -(slot["pc"] + 150 + eps_load) or p > 30
        if slot["mode"] == SLOT_PV_CHARGE:
            return grid_draw or p > 30
        # autoconsommation : au SOC mini, la batterie peut se recharger pour se protéger
        protect = soc <= slot["sl"] + 2
        return (grid_draw and not protect) or p > slot["pd"] + 150

    async def _monitor_tick(self, _now: datetime | None = None) -> None:
        now = dt_util.utcnow()
        bad = self.plan_deviation()
        if bad:
            self._bad_since = self._bad_since or now
        elif bad is False:
            # la batterie suit le planning : nouvelle tentative autorisée au prochain écart
            self._bad_since = None
            self._last_resend = None
        issue = self._bad_since is not None and (
            now - self._bad_since
        ).total_seconds() >= MONITOR_DELAY_S
        if issue != self.plan_issue:
            self.plan_issue = issue
            self.notify_listeners()
        if not issue:
            return
        if self.mode != MODE_PLANNING or self._send_lock.locked():
            return
        if self._last_resend and (now - self._last_resend).total_seconds() < MONITOR_RETRY_S:
            return
        self._last_resend = now
        slot = self.current_slot()
        persistent_notification.async_create(
            self.hass,
            (
                f"La batterie ne suivait pas le planning « {self.selected_plan} » "
                f"({dt_util.now().strftime('%d/%m %H:%M')}).\n"
                f"Créneau attendu : {describe_slot(slot) if slot else '?'}\n"
                f"Mesuré : batterie {self.q('bat_p')} W, port réseau {self.q('grid_on_p')} W, "
                f"SOC {self.q('soc')} %.\nLe planning local a été renvoyé."
            ),
            title="Batterie - planning local réappliqué",
            notification_id=f"{DOMAIN}_monitor",
        )
        await self.async_send_plan(notify=False)


def get_hub(hass: HomeAssistant, entry_id: str | None) -> HiBatteryHub:
    hubs: dict[str, HiBatteryHub] = hass.data.get(DOMAIN, {})
    if entry_id:
        if entry_id not in hubs:
            raise PlanError(f"Batterie inconnue : {entry_id}")
        return hubs[entry_id]
    if not hubs:
        raise PlanError("Aucune batterie configurée")
    return next(iter(hubs.values()))

