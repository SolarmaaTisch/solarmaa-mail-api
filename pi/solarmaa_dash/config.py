"""Konfiguration aus einer TOML-Datei (Standard: /etc/solarmaa-dash/config.toml)."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

DEFAULTS: dict = {
    "anlage": {
        "name": "Meine Solaranlage",
        "kwp": 10.0,
        "inbetriebnahme": "",
    },
    "installateur": {
        "name": "Solarmaa GmbH",
        "telefon": "",
        "email": "",
        "web": "www.solarmaa.ch",
    },
    "quelle": {
        "typ": "demo",          # "demo" oder "sunspec"
        "intervall_s": 5,
        "wechselrichter": {"host": "", "port": 502, "unit": 1},
        "zaehler": {"host": "", "port": 502, "unit": 240, "netz_vorzeichen": 1},
        "speicher": {"host": "", "port": 502, "unit": 1},
    },
    "auswertung": {
        # Optionale Werte. Leer lassen, wenn nicht bekannt: dann blendet die App
        # Ersparnis bzw. CO2 einfach aus, statt geschätzte Zahlen zu zeigen.
        "tarif_bezug_rp": None,
        "verguetung_rp": None,
        "co2_kg_pro_kwh": None,
    },
    "server": {"host": "0.0.0.0", "port": 80, "app_pfad": ""},
    "daten": {"pfad": "/var/lib/solarmaa-dash/data.db", "aufbewahrung_tage": 800},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def load(path: str | Path | None) -> dict:
    if path is None:
        return copy.deepcopy(DEFAULTS)
    with open(path, "rb") as fh:
        user = tomllib.load(fh)
    cfg = _merge(DEFAULTS, user)
    typ = cfg["quelle"]["typ"]
    if typ not in ("demo", "sunspec"):
        raise ValueError(f"quelle.typ muss 'demo' oder 'sunspec' sein, nicht {typ!r}")
    if typ == "sunspec" and not cfg["quelle"]["wechselrichter"]["host"]:
        raise ValueError("Für quelle.typ = 'sunspec' fehlt quelle.wechselrichter.host")
    return cfg
