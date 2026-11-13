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
    # Optional, neue Form: Liste von Geräten. Ersetzt [quelle.wechselrichter/zaehler/speicher].
    "geraet": [],
    "smartme": {"basis_url": "https://api.smart-me.com", "api_key": "", "benutzer": "", "passwort": "",
                "cache_s": 30},
    "easee": {"benutzer": "", "passwort": "", "basis_url": "https://api.easee.com"},
    "haus": {"aktiv": False},
    # Abregelung bei negativen Day-Ahead-Preisen (siehe docs/11-abregelung.md)
    "abregelung": {"modus": "aus"},
    # Alarme: rote Lampe (Shelly/myStrom), Push (ntfy), E-Mail – siehe docs/11-abregelung.md
    "benachrichtigung": {},
    # Solarmaa-Portal (siehe docs/13-portal.md)
    # fernwartung: Update und Konfiguration über das Portal zulassen (Standard aus, siehe ENTSCHEIDE E7)
    "portal": {"url": "", "schluessel": "", "token": "", "intervall_s": 60, "fernwartung": False},
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


def _adressen_pruefen(cfg: dict) -> None:
    """Internet-Adressen der Konfiguration (lokal und aus der Ferne gleich). Leer = nicht benutzt."""
    from .adressen import pruefe_url
    b = cfg.get("benachrichtigung") or {}
    push = b.get("push") if isinstance(b.get("push"), dict) else {}
    felder = [("smartme.basis_url", (cfg.get("smartme") or {}).get("basis_url"), ("https",)),
              ("easee.basis_url", (cfg.get("easee") or {}).get("basis_url"), ("https",)),
              ("benachrichtigung.push.server", push.get("server"), ("http", "https")),
              ("benachrichtigung.lebenszeichen_url", b.get("lebenszeichen_url"), ("http", "https"))]
    for was, url, schemata in felder:
        if url is None or url == "":
            continue
        pruefe_url(url, schemata, was)


def load(path: str | Path | None) -> dict:
    if path is None:
        return copy.deepcopy(DEFAULTS)
    with open(path, "rb") as fh:
        user = tomllib.load(fh)
    cfg = _merge(DEFAULTS, user)
    _adressen_pruefen(cfg)
    typ = cfg["quelle"]["typ"]
    if typ not in ("demo", "sunspec"):
        raise ValueError(f"quelle.typ muss 'demo' oder 'sunspec' sein, nicht {typ!r}")
    for g in cfg["geraet"]:
        if not g.get("typ"):
            raise ValueError(f"[[geraet]] {g.get('name', '?')}: typ fehlt")
    if typ == "sunspec" and not cfg["geraet"] and not cfg["quelle"]["wechselrichter"]["host"]:
        raise ValueError("Für quelle.typ = 'sunspec' fehlt quelle.wechselrichter.host")
    return cfg
