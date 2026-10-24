"""Constantes de l'intégration Hoymiles HiBattery (MQTT local)."""

from __future__ import annotations

DOMAIN = "hoymiles_hibattery"
VERSION = "1.0.4"

CONF_DEVICE_ID = "device_id"
CONF_PREFIX = "topic_prefix"
CONF_MODEL = "model"
CONF_CAPACITY = "capacity_kwh"
CONF_GRID_IMPORT = "grid_import_entity"
CONF_GRID_EXPORT = "grid_export_entity"

DEFAULT_DEVICE_ID = "MSA-280526231099"
DEFAULT_PREFIX = "homeassistant"
DEFAULT_MODEL = "HiBattery 4020 X"
DEFAULT_CAPACITY = 4.0

# Modes de pilotage (sélecteur)
MODE_PLANNING = "Planning"
MODE_MANUAL = "Manuel"
MODE_AUTO = "Auto (Hoymiles)"
MODES = [MODE_PLANNING, MODE_MANUAL, MODE_AUTO]
MODE_TO_EMS = {
    MODE_PLANNING: "tou_plan",
    MODE_MANUAL: "mqtt_ctrl",
    MODE_AUTO: "general",
}

# Modes des créneaux TOU (valeurs MQTT de la batterie)
SLOT_FORCED_CHARGE = 1
SLOT_PV_CHARGE = 2
SLOT_SELF_CONSUMPTION = 4
SLOT_MODE_NAMES = {
    SLOT_FORCED_CHARGE: "Charge forcée",
    SLOT_PV_CHARGE: "Recharge prioritaire PV",
    SLOT_SELF_CONSUMPTION: "Autoconsommation",
}

WEEK_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Délais
MANUAL_INTERVAL_S = 10          # la batterie exige une consigne < 60 s
ACK_TIMEOUT_S = 20
MONITOR_INTERVAL_S = 30
MONITOR_DELAY_S = 180           # écart toléré avant de réagir
MONITOR_RETRY_S = 1800          # nouvelle tentative si l'écart persiste
QUICK_STALE_S = 300             # mesure considérée périmée
PV_SAVE_INTERVAL_S = 300        # sauvegarde de l'énergie PV cumulée

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"

URL_BASE = f"/{DOMAIN}_static"
CARD_FILE = "hoymiles-tou-card.js"
CARDS_FILE = "hoymiles-cards.js"
