"""Tests de bout en bout avec un Home Assistant de test et un MQTT simulé."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

from custom_components.hoymiles_hibattery.const import DOMAIN
from custom_components.hoymiles_hibattery.plans import (
    PlanError,
    to_day_plan,
    validate_slots,
)

DEV = "MSA-280526231099"
BASE = f"homeassistant/sensor/{DEV}"
QUICK = {
    "grid_on_p": 200.1, "grid_off_p": -150.0, "pv_p": 0, "bat_sts": "discharge",
    "bat_p": 200.1, "soc": 64.1, "heat": False,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


AUTO_REPLY = {
    "tou_day_plan/set": ("tou_day_plan/ack", '{"status":0,"err_msg":"success"}'),
    "tou_week_plan/set": ("tou_week_plan/ack", '{"status":0,"err_msg":"success"}'),
}


def battery_replies(hass, mqtt_mock, replies):
    """La batterie simulée répond aux publications (comme la vraie)."""
    original = mqtt_mock.async_publish.side_effect

    async def _pub(topic, payload, *args, **kwargs):
        for end, (reply_end, reply) in replies.items():
            if topic.endswith(end):
                hass.loop.call_soon(async_fire_mqtt_message, hass, f"{BASE}/{reply_end}", reply)
        if original:
            res = original(topic, payload, *args, **kwargs)
            if asyncio.iscoroutine(res):
                await res
        return None

    mqtt_mock.async_publish.side_effect = _pub


@pytest.fixture
async def setup(hass: HomeAssistant, mqtt_mock):
    hass.config.components.add("http")  # la carte est servie seulement si http est chargé
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEV,
        data={"device_id": DEV, "topic_prefix": "homeassistant", "model": "HiBattery 4020 X",
              "capacity_kwh": 4.0},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.hoymiles_hibattery._register_frontend"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry, mqtt_mock


def published(mqtt_mock, topic_end: str) -> list[str]:
    return [c.args[1] for c in mqtt_mock.async_publish.call_args_list if c.args[0].endswith(topic_end)]


def test_plan_validation():
    ok = validate_slots([
        {"mode": 4, "debut": "00:00", "fin": "06:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200},
        {"mode": 1, "debut": "06:00", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200},
    ])
    assert to_day_plan(ok)["day_plan"][1] == {"mode": 1, "ts": 24, "te": 96, "sh": 100, "sl": 10, "pc": 800, "pd": 200}
    with pytest.raises(PlanError, match="Trou"):
        validate_slots([{"mode": 4, "debut": "00:00", "fin": "05:00"} | dict(sh=100, sl=10, pc=800, pd=200),
                        {"mode": 4, "debut": "06:00", "fin": "24:00"} | dict(sh=100, sl=10, pc=800, pd=200)])
    with pytest.raises(PlanError, match="15 min"):
        validate_slots([{"mode": 4, "debut": "00:00", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200},
                        {"mode": 4, "debut": "00:10", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200}])
    with pytest.raises(PlanError, match="puissance"):
        validate_slots([{"mode": 4, "debut": "00:00", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 5000}])


async def test_entities_and_values(hass: HomeAssistant, setup):
    async_fire_mqtt_message(hass, f"{BASE}/quick/state", json.dumps(QUICK))
    async_fire_mqtt_message(hass, f"{BASE}/system/state", json.dumps({"ems_mode": "tou_plan", "pv_e": 312, "chg_e": 0}))
    async_fire_mqtt_message(hass, f"{BASE}/device/state", json.dumps(
        {"grid": [{"type": "grid_on", "v": 226.1, "f": 49.97}], "bat_temp": 29, "rssi": -43, "pack_num": 1}))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get("sensor.msa_280526231099_soc").state == "64.1"
    assert hass.states.get("sensor.msa_280526231099_puissance_batterie").state == "200.1"
    assert hass.states.get("sensor.msa_280526231099_puissance_pv_totale").state == "150.0"
    assert hass.states.get("sensor.msa_280526231099_energie_disponible").state == "2.56"
    assert hass.states.get("sensor.msa_280526231099_mode_ems_actuel").state == "tou_plan"
    assert hass.states.get("sensor.msa_280526231099_temperature_batterie").state == "29.0"
    assert hass.states.get("binary_sensor.msa_280526231099_chauffage_batterie").state == "off"
    assert hass.states.get("select.msa_280526231099_mode_batterie").state == "Planning"
    plan_sel = hass.states.get("select.msa_280526231099_planning_tou_choisi")
    assert plan_sel.state == "Hiver Soleil"
    assert plan_sel.attributes["options"] == ["Hiver Soleil", "Hiver cloud", "Ete Soleil", "Ete cloud"]
    planning = hass.states.get("sensor.msa_280526231099_planning_tou")
    assert len(planning.attributes["plans"]) == 4 and planning.attributes["entry_id"]


async def test_save_select_and_send(hass: HomeAssistant, setup):
    entry, mqtt_mock = setup
    slots = [
        {"mode": 4, "debut": "00:00", "fin": "12:00", "sh": 100, "sl": 10, "pc": 800, "pd": 300},
        {"mode": 2, "debut": "12:00", "fin": "24:00", "sh": 90, "sl": 10, "pc": 800, "pd": 300},
    ]
    await hass.services.async_call(DOMAIN, "save_plan", {"name": "Test", "slots": slots}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "Test" in hass.states.get("select.msa_280526231099_planning_tou_choisi").attributes["options"]

    # choisir le planning -> envoi jour + semaine + ems
    battery_replies(hass, mqtt_mock, AUTO_REPLY)
    await hass.services.async_call(DOMAIN, "select_plan", {"name": "Test"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    day = published(mqtt_mock, "tou_day_plan/set")
    assert json.loads(day[-1])["day_plan"][1] == {"mode": 2, "ts": 48, "te": 96, "sh": 90, "sl": 10, "pc": 800, "pd": 300}
    week = json.loads(published(mqtt_mock, "tou_week_plan/set")[-1])
    assert week == {"week_plan": [{"week": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "day_idx": 1}]}
    assert published(mqtt_mock, "ems_mode/command")[-1] == "tou_plan"
    assert hass.states.get("select.msa_280526231099_planning_tou_choisi").state == "Test"
    assert "Test" in hass.states.get("sensor.msa_280526231099_planning_tou").attributes["dernier_resultat"]

    # renommer puis supprimer
    await hass.services.async_call(DOMAIN, "save_plan",
                                   {"name": "Test2", "old_name": "Test", "slots": slots}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("select.msa_280526231099_planning_tou_choisi").state == "Test2"
    await hass.services.async_call(DOMAIN, "delete_plan", {"name": "Test2"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("select.msa_280526231099_planning_tou_choisi").state == "Hiver Soleil"

    # planning invalide -> erreur lisible
    with pytest.raises(ServiceValidationError, match="24:00"):
        await hass.services.async_call(DOMAIN, "save_plan", {"name": "X", "slots": slots[:1]}, blocking=True)


async def test_send_timeout(hass: HomeAssistant, setup):
    with patch("custom_components.hoymiles_hibattery.hub.ACK_TIMEOUT_S", 0.05):
        resp = await hass.services.async_call(DOMAIN, "send_plan", {}, blocking=True, return_response=True)
    assert resp["ok"] is False and "Pas de réponse" in resp["message"]


async def test_read_plan(hass: HomeAssistant, setup):
    entry, mqtt_mock = setup
    status = json.dumps({"week": "Sat", "day_idx": 1, "status": 0, "err_msg": "success",
                         "day_plan": [{"mode": 1, "ts": 0, "te": 96, "sh": 100, "sl": 10, "pc": 200, "pd": 200}]})
    battery_replies(hass, mqtt_mock, {"tou_plan/get": ("tou_plan/status", status)})
    resp = await hass.services.async_call(
        DOMAIN, "read_plan", {"day": "Sat", "notify": False}, blocking=True, return_response=True)
    assert resp["ok"] and resp["slots"][0]["fin"] == "24:00" and "Charge forcée" in resp["message"]


async def test_manual_mode(hass: HomeAssistant, setup):
    entry, mqtt_mock = setup
    async_fire_mqtt_message(hass, f"{BASE}/quick/state", json.dumps(QUICK))
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.msa_280526231099_consigne_de_decharge", "value": 450},
                                   blocking=True)
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.msa_280526231099_decharge_max", "value": 300}, blocking=True)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.msa_280526231099_mode_batterie", "option": "Manuel"},
                                   blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert published(mqtt_mock, "ems_mode/command")[-1] == "mqtt_ctrl"
    assert published(mqtt_mock, "power_ctrl/set")[-1] == "300"  # plafonné par la décharge max
    # renvoi périodique
    n = len(published(mqtt_mock, "power_ctrl/set"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(published(mqtt_mock, "power_ctrl/set")) > n
    # SOC sous le minimum -> 0
    async_fire_mqtt_message(hass, f"{BASE}/quick/state", json.dumps(QUICK | {"soc": 8}))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=22))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert published(mqtt_mock, "power_ctrl/set")[-1] == "0"
    assert hass.states.get("sensor.msa_280526231099_consigne_envoyee").state == "0.0"


async def test_monitor_resends(hass: HomeAssistant, setup, freezer):
    entry, mqtt_mock = setup
    battery_replies(hass, mqtt_mock, AUTO_REPLY)
    hub = hass.data[DOMAIN][entry.entry_id]
    # créneau autoconsommation en cours, mais la batterie charge depuis le réseau (planning cloud)
    hub.plans["Hiver Soleil"] = validate_slots(
        [{"mode": 4, "debut": "00:00", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200}])
    bad = QUICK | {"bat_p": -300, "grid_on_p": -300, "bat_sts": "charge"}
    sensor = "binary_sensor.msa_280526231099_planning_tou_non_respecte"

    async def step(seconds, quick):
        freezer.tick(timedelta(seconds=seconds))
        async_fire_mqtt_message(hass, f"{BASE}/quick/state", json.dumps(quick))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    await step(1, bad)
    assert hub.plan_deviation() is True
    await step(31, bad)
    assert hass.states.get(sensor).state == "off"      # écart trop court
    sent_before = len(published(mqtt_mock, "tou_day_plan/set"))
    for _ in range(6):
        await step(31, bad)                            # > 3 min d'écart
    assert len(published(mqtt_mock, "tou_day_plan/set")) == sent_before + 1  # renvoyé une fois
    for _ in range(4):
        await step(31, bad)
    assert len(published(mqtt_mock, "tou_day_plan/set")) == sent_before + 1  # pas de rafale
    await step(31, QUICK)                              # la batterie suit à nouveau
    assert hass.states.get(sensor).state == "off"


async def test_daily_counter(hass: HomeAssistant, setup):
    async_fire_mqtt_message(hass, f"{BASE}/system/state", json.dumps({"chg_e": 1000}))
    await hass.async_block_till_done(wait_background_tasks=True)
    async_fire_mqtt_message(hass, f"{BASE}/system/state", json.dumps({"chg_e": 1250}))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.msa_280526231099_batterie_charge_jour").state == "0.25"
    # remise à zéro du compteur de la batterie
    async_fire_mqtt_message(hass, f"{BASE}/system/state", json.dumps({"chg_e": 100}))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.msa_280526231099_batterie_charge_jour").state == "0.35"


async def test_config_flow(hass: HomeAssistant, mqtt_mock):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    with patch("custom_components.hoymiles_hibattery.async_setup_entry", return_value=True), \
         patch("custom_components.hoymiles_hibattery._register_frontend"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"device_id": "MSA-1", "topic_prefix": "homeassistant", "model": "HiBattery", "capacity_kwh": 4},
        )
    assert result["type"] == "create_entry" and result["data"]["device_id"] == "MSA-1"


async def test_example_cards_entities_exist(hass: HomeAssistant, mqtt_mock):
    """Toutes les entités utilisées par les cartes d'exemple existent."""
    import re
    from pathlib import Path

    hass.states.async_set("sensor.shelly_import", "10", {"unit_of_measurement": "kWh", "device_class": "energy"})
    hass.states.async_set("sensor.shelly_export", "2", {"unit_of_measurement": "kWh", "device_class": "energy"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEV, data={
        "device_id": DEV, "topic_prefix": "homeassistant", "model": "HiBattery 4020 X", "capacity_kwh": 4.0,
        "grid_import_entity": "sensor.shelly_import", "grid_export_entity": "sensor.shelly_export"})
    entry.add_to_hass(hass)
    with patch("custom_components.hoymiles_hibattery._register_frontend"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    created = set(hass.states.async_entity_ids())
    used = set()
    for f in (Path(__file__).parent.parent / "examples").glob("*.yaml"):
        used |= set(re.findall(r"\b(?:sensor|select|number|binary_sensor|button)\.msa_\w+", f.read_text()))
    assert used and not (used - created), sorted(used - created)
    # consommation maison = achat - injection + sortie - entrée
    hass.states.async_set("sensor.shelly_import", "11.5", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.msa_280526231099_reseau_achat_jour").state == "1.5"
    assert hass.states.get("sensor.msa_280526231099_consommation_maison_jour").state == "1.5"


async def test_monitor_retry_throttle(hass: HomeAssistant, setup, freezer):
    """Écart persistant : un seul renvoi par tranche de 30 min."""
    entry, mqtt_mock = setup
    battery_replies(hass, mqtt_mock, AUTO_REPLY)
    hub = hass.data[DOMAIN][entry.entry_id]
    hub.plans["Hiver Soleil"] = validate_slots(
        [{"mode": 4, "debut": "00:00", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200}])
    bad = QUICK | {"bat_p": -300, "grid_on_p": -300, "bat_sts": "charge"}
    sensor = "binary_sensor.msa_280526231099_planning_tou_non_respecte"
    before = len(published(mqtt_mock, "tou_day_plan/set"))

    async def run(minutes, quick):
        for _ in range(minutes * 2):
            freezer.tick(timedelta(seconds=30))
            async_fire_mqtt_message(hass, f"{BASE}/quick/state", json.dumps(quick))
            async_fire_time_changed(hass)
            await hass.async_block_till_done(wait_background_tasks=True)

    await run(25, bad)
    assert len(published(mqtt_mock, "tou_day_plan/set")) == before + 1
    assert hass.states.get(sensor).state == "on"       # l'écart reste signalé
    await run(10, bad)                                  # > 30 min après le premier renvoi
    assert len(published(mqtt_mock, "tou_day_plan/set")) == before + 2
    await run(1, QUICK)                                 # la batterie suit à nouveau
    assert hass.states.get(sensor).state == "off"


async def test_monitor_ignores_heating_and_eps(hass: HomeAssistant, setup):
    entry, _ = setup
    hub = hass.data[DOMAIN][entry.entry_id]
    hub.plans["Hiver Soleil"] = validate_slots(
        [{"mode": 4, "debut": "00:00", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200}])

    def deviation(**quick):
        async_fire_mqtt_message(hass, f"{BASE}/quick/state", json.dumps(QUICK | quick))
        return hub.plan_deviation()

    assert deviation(bat_p=0, grid_on_p=-300) is True                      # charge réseau
    assert deviation(bat_p=0, grid_on_p=-300, heat=True) is None           # chauffage
    assert deviation(bat_p=0, grid_on_p=-300, heat="0") is True            # "0" = pas de chauffage
    assert deviation(bat_p=0, grid_on_p=-300, grid_off_p=350) is False     # appareil sur la prise EPS
    assert deviation(bat_p=0, grid_on_p=-300, soc=11) is False             # protection au SOC mini


async def test_save_reports_send_failure(hass: HomeAssistant, setup):
    """Planning actif enregistré mais batterie muette : l'erreur remonte à la carte."""
    entry, _ = setup
    hub = hass.data[DOMAIN][entry.entry_id]
    slots = [{"mode": 4, "debut": "00:00", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200}]
    with patch("custom_components.hoymiles_hibattery.hub.ACK_TIMEOUT_S", 0.05):
        with pytest.raises(HomeAssistantError, match="pas envoyé"):
            await hass.services.async_call(
                DOMAIN, "save_plan", {"name": "Hiver Soleil", "slots": slots}, blocking=True)
        with pytest.raises(HomeAssistantError, match="Pas de réponse"):
            await hass.services.async_call(DOMAIN, "send_plan", {}, blocking=True)
    assert hub.plans["Hiver Soleil"][0]["fin"] == "24:00"   # enregistré quand même


async def test_store_saved_despite_frequent_messages(hass: HomeAssistant, setup, freezer, hass_storage):
    """Les messages quick/state fréquents ne doivent pas empêcher l'écriture des réglages."""
    entry, _ = setup
    slots = [{"mode": 4, "debut": "00:00", "fin": "24:00", "sh": 100, "sl": 10, "pc": 800, "pd": 200}]
    await hass.services.async_call(DOMAIN, "save_plan", {"name": "Nouveau", "slots": slots}, blocking=True)
    for _ in range(10):
        freezer.tick(timedelta(seconds=2))
        async_fire_mqtt_message(hass, f"{BASE}/quick/state", json.dumps(QUICK))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert "Nouveau" in hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["plans"]


async def test_daily_counter_ignores_zero_glitch(hass: HomeAssistant, setup):
    for value in (1000, 1250, 0, 1250, 1300):
        async_fire_mqtt_message(hass, f"{BASE}/system/state", json.dumps({"chg_e": value}))
        await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("sensor.msa_280526231099_batterie_charge_jour")
    assert state.state == "0.3" and state.attributes["derniere_valeur_source"] == 1300


async def test_send_other_plan_selects_it(hass: HomeAssistant, setup):
    entry, mqtt_mock = setup
    battery_replies(hass, mqtt_mock, AUTO_REPLY)
    resp = await hass.services.async_call(
        DOMAIN, "send_plan", {"name": "Hiver cloud"}, blocking=True, return_response=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert resp["ok"]
    assert hass.states.get("select.msa_280526231099_planning_tou_choisi").state == "Hiver cloud"


async def test_options_can_clear_meter(hass: HomeAssistant, setup):
    entry, _ = setup
    hass.config_entries.async_update_entry(entry, options={
        "capacity_kwh": 4.0, "grid_import_entity": "sensor.shelly_import"})
    await hass.async_block_till_done(wait_background_tasks=True)
    with patch("custom_components.hoymiles_hibattery._register_frontend"):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"capacity_kwh": 4.0})
        await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] == "create_entry"
    assert "grid_import_entity" not in entry.options


# messages réels capturés sur la batterie (firmware 1.3.3)
QUICK_133 = {
    "grid_on_p": 766.5, "grid_off_p": -584.9, "pv_p": 0, "bat_sts": "discharge", "bat_p": 181.6, "soc": 76.5,
    "heat": False, "sys_pv_p": 0, "sys_plug_p": 766.5, "sys_bat_p": 181.6, "sys_grid_p": 459.1,
    "sys_load_p": 1225.6, "sys_sp_p": 0, "sys_soc": 76.5, "sys_heat": False, "sys_pv2_p": 0, "sys_eps_p": -584.9,
}
SYSTEM_133 = {
    "pv_p": 0, "pv2_p": 0, "plug_p": 715.1, "bat_p": 87.4, "grid_p": 263, "load_p": 978.1, "sp_p": 0,
    "eps_p": -627.7, "soc": 76.7, "pv_e": 0, "pv2_e": 0, "dchg_e": 1209, "chg_e": 2281, "plug_out_e": 1507,
    "plug_in_e": 2573, "eps_out_e": 0, "eps_in_e": 556, "ems_mode": "tou_plan",
}
DEVICE_133 = {
    "grid": [
        {"type": "grid_on", "v": 233.1, "i": 3.58, "f": 50.01, "p": 715.1, "q": 145.9,
         "ein": 2573, "eout": 1509, "etin": 70049, "etout": 84465},
        {"type": "grid_off", "v": 235.3, "i": 3.18, "f": 50.01, "p": -627.7, "q": -36.9,
         "ein": 557, "eout": 0, "etin": 40985, "etout": 0},
        {"type": "inv", "v": 233.7, "i": 0.72, "p": 87.4, "q": 108.9,
         "ein": 2579, "eout": 966, "etin": 80876, "etout": 54519},
    ],
    "bat_sts": "discharge", "bat_v": 13.2, "bat_i": -8.28, "bat_p": 87.4, "bat_temp": 20.5, "soc": 76.7,
    "rssi": -46, "pv_num": 4, "pvs": [{"id": i, "p": 0} for i in range(1, 5)], "pack_num": 1,
    "packs": [{"id": 1, "soc": 76.6, "temp": 20.5, "heat": False}],
}


async def test_firmware_133_fields(hass: HomeAssistant, setup):
    async_fire_mqtt_message(hass, f"{BASE}/quick/state", json.dumps(QUICK_133))
    async_fire_mqtt_message(hass, f"{BASE}/system/state", json.dumps(SYSTEM_133))
    async_fire_mqtt_message(hass, f"{BASE}/device/state", json.dumps(DEVICE_133))
    await hass.async_block_till_done(wait_background_tasks=True)
    expected = {
        "puissance_reseau_compteur": "459.1",
        "puissance_consommation_maison": "1225.6",
        "puissance_pv_totale": "584.9",
        "energie_eps_entree": "556.0",
        "energie_eps_sortie": "0.0",
        "energie_prise_entree_totale": "70049.0",
        "energie_prise_sortie_totale": "84465.0",
        "energie_eps_entree_totale": "40985.0",
        "energie_eps_sortie_totale": "0.0",
        "tension_reseau": "233.1",
    }
    for key, value in expected.items():
        assert hass.states.get(f"sensor.msa_280526231099_{key}").state == value, key
