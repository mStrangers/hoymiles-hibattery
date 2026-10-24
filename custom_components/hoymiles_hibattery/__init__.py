"""Intégration Hoymiles HiBattery (MQTT local)."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CARD_FILE, CARDS_FILE, DOMAIN, URL_BASE, VERSION, WEEK_DAYS
from .hub import HiBatteryHub, get_hub
from .plans import PlanError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_ENTRY_ID = "entry_id"

SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.Coerce(int),
        vol.Required("debut"): cv.string,
        vol.Required("fin"): cv.string,
        vol.Optional("sh", default=100): vol.Coerce(int),
        vol.Optional("sl", default=10): vol.Coerce(int),
        vol.Optional("pc", default=800): vol.Coerce(int),
        vol.Optional("pd", default=200): vol.Coerce(int),
    },
    extra=vol.REMOVE_EXTRA,
)

SAVE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required("name"): cv.string,
        vol.Required("slots"): vol.All(cv.ensure_list, [SLOT_SCHEMA]),
        vol.Optional("old_name"): cv.string,
        vol.Optional("select", default=False): cv.boolean,
    }
)
NAME_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_ENTRY_ID): cv.string, vol.Required("name"): cv.string}
)
SEND_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_ENTRY_ID): cv.string, vol.Optional("name"): cv.string}
)
READ_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional("day", default="Mon"): vol.In(WEEK_DAYS),
        vol.Optional("notify", default=True): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services et carte (une seule fois)."""
    hass.data.setdefault(DOMAIN, {})
    await _register_frontend(hass)

    def _hub(call: ServiceCall) -> HiBatteryHub:
        try:
            return get_hub(hass, call.data.get(ATTR_ENTRY_ID))
        except PlanError as err:
            raise ServiceValidationError(str(err)) from err

    async def save_plan(call: ServiceCall) -> ServiceResponse:
        hub = _hub(call)
        try:
            name, sent = await hub.async_save_plan(
                call.data["name"],
                call.data["slots"],
                select=call.data["select"],
                old_name=call.data.get("old_name"),
            )
        except PlanError as err:
            raise ServiceValidationError(str(err)) from err
        if sent is False:
            raise HomeAssistantError(
                f"Planning « {name} » enregistré, mais pas envoyé à la batterie : {hub.last_plan_result}"
            )
        return {"name": name, "sent": sent}

    async def delete_plan(call: ServiceCall) -> None:
        try:
            await _hub(call).async_delete_plan(call.data["name"])
        except PlanError as err:
            raise ServiceValidationError(str(err)) from err

    async def select_plan(call: ServiceCall) -> None:
        hub = _hub(call)
        try:
            sent = await hub.async_select_plan(call.data["name"])
        except PlanError as err:
            raise ServiceValidationError(str(err)) from err
        if sent is False:
            raise HomeAssistantError(
                f"Planning choisi, mais pas envoyé à la batterie : {hub.last_plan_result}"
            )

    async def send_plan(call: ServiceCall) -> ServiceResponse:
        hub = _hub(call)
        ok = await hub.async_send_plan(call.data.get("name"), notify=True)
        if not ok and not call.return_response:
            # appel sans réponse (carte, automatisation) : l'échec doit être visible
            raise HomeAssistantError(hub.last_plan_result or "Échec de l'envoi du planning")
        return {"ok": ok, "message": hub.last_plan_result}

    async def read_plan(call: ServiceCall) -> ServiceResponse:
        try:
            return await _hub(call).async_read_plan(call.data["day"], notify=call.data["notify"])
        except PlanError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(
        DOMAIN, "save_plan", save_plan, SAVE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, "delete_plan", delete_plan, NAME_SCHEMA)
    hass.services.async_register(DOMAIN, "select_plan", select_plan, NAME_SCHEMA)
    hass.services.async_register(
        DOMAIN, "send_plan", send_plan, SEND_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "read_plan", read_plan, READ_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    return True


async def _register_frontend(hass: HomeAssistant) -> None:
    """Sert la carte et la charge automatiquement dans l'interface."""
    if (
        "http" not in hass.config.components
        or "frontend" not in hass.config.components
        or hass.data.get(f"{DOMAIN}_frontend")
    ):
        return
    path = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths([StaticPathConfig(URL_BASE, str(path), False)])
    try:
        from homeassistant.components.frontend import add_extra_js_url

        for file in (CARD_FILE, CARDS_FILE):
            add_extra_js_url(hass, f"{URL_BASE}/{file}?v={VERSION}")
    except ImportError:
        _LOGGER.warning("Frontend indisponible : la carte n'a pas pu être chargée")
    hass.data[f"{DOMAIN}_frontend"] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT n'est pas disponible")
    hub = HiBatteryHub(hass, entry)
    await hub.async_setup()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hub: HiBatteryHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_unload()
    return ok
