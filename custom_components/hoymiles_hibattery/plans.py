"""Plannings TOU : valeurs par défaut, validation et conversion au format MQTT."""

from __future__ import annotations

from typing import Any

from .const import SLOT_FORCED_CHARGE, SLOT_MODE_NAMES, SLOT_PV_CHARGE

MAX_SLOTS = 12
MAX_PLANS = 20


def _slot(mode: int, debut: str, fin: str, pc: int = 800, pd: int = 200) -> dict[str, Any]:
    return {"mode": mode, "debut": debut, "fin": fin, "sh": 100, "sl": 10, "pc": pc, "pd": pd}


def _hiver_soleil() -> list[dict[str, Any]]:
    return [
        _slot(4, "00:00", "01:45"),
        _slot(1, "01:45", "07:45"),
        _slot(4, "07:45", "11:15"),
        _slot(4, "11:15", "12:00", pd=750),
        _slot(4, "12:00", "15:15"),
        _slot(2, "15:15", "17:15"),
        _slot(4, "17:15", "24:00"),
    ]


def _hiver_cloud() -> list[dict[str, Any]]:
    slots = _hiver_soleil()
    slots[5] = _slot(1, "15:15", "17:15")
    return slots


def default_plans() -> dict[str, list[dict[str, Any]]]:
    """Plannings créés au premier démarrage."""
    return {
        "Hiver Soleil": _hiver_soleil(),
        "Hiver cloud": _hiver_cloud(),
        "Ete Soleil": _hiver_soleil(),
        "Ete cloud": _hiver_cloud(),
    }


class PlanError(ValueError):
    """Planning invalide (message lisible pour l'utilisateur)."""


def hhmm_to_quarters(value: str) -> int:
    """'01:45' -> 7 (quarts d'heure)."""
    try:
        hours, minutes = str(value).strip().split(":")
        h, m = int(hours), int(minutes)
    except (ValueError, AttributeError) as err:
        raise PlanError(f"Heure invalide : {value!r} (format HH:MM attendu)") from err
    if m % 15 or not 0 <= m < 60 or not 0 <= h <= 24 or (h == 24 and m):
        raise PlanError(f"Heure invalide : {value} (pas de 15 min, de 00:00 à 24:00)")
    return h * 4 + m // 15


def quarters_to_hhmm(value: int) -> str:
    return f"{value // 4:02d}:{(value % 4) * 15:02d}"


def _int_in(slot: dict, key: str, low: int, high: int, label: str, index: int) -> int:
    try:
        val = int(float(slot.get(key)))
    except (TypeError, ValueError) as err:
        raise PlanError(f"Créneau {index} : {label} manquant ou invalide") from err
    if not low <= val <= high:
        raise PlanError(f"Créneau {index} : {label} doit être entre {low} et {high}")
    return val


def validate_slots(slots: Any, max_power: int = 1000) -> list[dict[str, Any]]:
    """Valide et normalise une liste de créneaux. Lève PlanError si invalide."""
    if not isinstance(slots, list) or not slots:
        raise PlanError("Le planning doit contenir au moins un créneau")
    if len(slots) > MAX_SLOTS:
        raise PlanError(f"La batterie accepte au maximum {MAX_SLOTS} créneaux")

    clean: list[dict[str, Any]] = []
    for i, raw in enumerate(slots, start=1):
        if not isinstance(raw, dict):
            raise PlanError(f"Créneau {i} invalide")
        mode = _int_in(raw, "mode", 1, 4, "le mode", i)
        if mode not in SLOT_MODE_NAMES:
            raise PlanError(f"Créneau {i} : mode {mode} inconnu (1, 2 ou 4)")
        ts = hhmm_to_quarters(raw.get("debut", ""))
        te = hhmm_to_quarters(raw.get("fin", ""))
        if te <= ts:
            raise PlanError(f"Créneau {i} : la fin doit être après le début")
        clean.append(
            {
                "mode": mode,
                "debut": quarters_to_hhmm(ts),
                "fin": quarters_to_hhmm(te),
                "sh": _int_in(raw, "sh", 10, 100, "le SOC de fin de charge", i),
                "sl": _int_in(raw, "sl", 10, 100, "le SOC de fin de décharge", i),
                "pc": _int_in(raw, "pc", 100, max_power, "la puissance de charge", i),
                "pd": _int_in(raw, "pd", 100, max_power, "la puissance de décharge", i),
            }
        )

    clean.sort(key=lambda s: hhmm_to_quarters(s["debut"]))
    if clean[0]["debut"] != "00:00":
        raise PlanError("Le premier créneau doit commencer à 00:00")
    if clean[-1]["fin"] != "24:00":
        raise PlanError("Le dernier créneau doit finir à 24:00")
    for prev, nxt in zip(clean, clean[1:]):
        if prev["fin"] != nxt["debut"]:
            if hhmm_to_quarters(prev["fin"]) > hhmm_to_quarters(nxt["debut"]):
                raise PlanError(f"Les créneaux se chevauchent vers {nxt['debut']}")
            raise PlanError(f"Trou dans le planning entre {prev['fin']} et {nxt['debut']}")
    return clean


def validate_name(name: Any) -> str:
    name = str(name or "").strip()
    if not name:
        raise PlanError("Le nom du planning est vide")
    if len(name) > 40:
        raise PlanError("Le nom du planning est trop long (40 caractères max)")
    return name


def to_day_plan(slots: list[dict[str, Any]], day_idx: int = 1) -> dict[str, Any]:
    """Format attendu par tou_day_plan/set."""
    return {
        "day_idx": day_idx,
        "day_plan": [
            {
                "mode": s["mode"],
                "ts": hhmm_to_quarters(s["debut"]),
                "te": hhmm_to_quarters(s["fin"]),
                "sh": s["sh"],
                "sl": s["sl"],
                "pc": s["pc"],
                "pd": s["pd"],
            }
            for s in slots
        ],
    }


def from_day_plan(day_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format renvoyé par tou_plan/status -> créneaux lisibles."""
    return [
        {
            "mode": int(p.get("mode", 4)),
            "debut": quarters_to_hhmm(int(p.get("ts", 0))),
            "fin": quarters_to_hhmm(int(p.get("te", 96))),
            "sh": int(p.get("sh", 100)),
            "sl": int(p.get("sl", 10)),
            "pc": int(p.get("pc", 800)),
            "pd": int(p.get("pd", 200)),
        }
        for p in day_plan
    ]


def describe_slot(slot: dict[str, Any]) -> str:
    name = SLOT_MODE_NAMES.get(slot["mode"], f"mode {slot['mode']}")
    if slot["mode"] == SLOT_FORCED_CHARGE:
        extra = f"jusqu'à {slot['sh']} %, {slot['pc']} W"
    elif slot["mode"] == SLOT_PV_CHARGE:
        extra = f"jusqu'à {slot['sh']} %"
    else:
        extra = f"jusqu'à {slot['sl']} %, {slot['pd']} W max"
    return f"{slot['debut']} → {slot['fin']} : {name} ({extra})"


def current_slot(slots: list[dict[str, Any]], minutes_of_day: int) -> dict[str, Any] | None:
    for slot in slots:
        start = hhmm_to_quarters(slot["debut"]) * 15
        end = hhmm_to_quarters(slot["fin"]) * 15
        if start <= minutes_of_day < end:
            return slot
    return None
