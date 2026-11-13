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


def _adressen_pruefen(cfg: dict, streng: bool) -> None:
    """Internet-Adressen der Konfiguration. Leer oder false = nicht benutzt (so galten diese Werte schon
    bisher, z. B. lebenszeichen_url = false). Meldungen nennen nur Feld und Regel, nie den Wert.

    streng (Speichern, lokal und aus der Ferne): ungültige Adresse = Fehler.
    Sonst (Laden einer bestehenden Datei beim Start): kein Abbruch, denn solche Werte waren vor 1.6.0
    startfähig. Push bzw. Lebenszeichen werden ausgeschaltet, smart-me/Easee melden beim Lesen einen
    Gerätefehler; die Warnung steht in cfg["_warnungen"] und wird beim Start als Alarm gemeldet."""
    from .adressen import pruefe_url
    b = cfg.get("benachrichtigung") or {}
    push = b.get("push") if isinstance(b.get("push"), dict) else {}
    felder = [("smartme.basis_url", (cfg.get("smartme") or {}).get("basis_url"), ("https",)),
              ("easee.basis_url", (cfg.get("easee") or {}).get("basis_url"), ("https",)),
              ("benachrichtigung.push.server", push.get("server"), ("http", "https")),
              ("benachrichtigung.lebenszeichen_url", b.get("lebenszeichen_url"), ("http", "https"))]
    warnungen = []
    for was, url, schemata in felder:
        if url is None or url is False or (isinstance(url, str) and not url.strip()):
            continue
        try:
            if was.endswith("push.server") and isinstance(url, str) and "://" not in url:
                # Der bisherige Code setzte kein Schema voran: Solche Werte haben nie eine Meldung zugestellt.
                raise ValueError(f"{was}: Adresse ohne Schema, bitte https:// voranstellen")
            pruefe_url(url, schemata, was)
        except ValueError as e:
            if streng:
                raise
            warnungen.append(f"{e}. Bis zur Korrektur ausgeschaltet.")
            if was.endswith("push.server"):
                b["push"] = {}                       # Push aus (nicht stillschweigend an einen anderen Server)
            elif was.endswith("lebenszeichen_url"):
                b["lebenszeichen_url"] = None
    if warnungen:
        cfg["_warnungen"] = warnungen


def load(path: str | Path | None, streng: bool = False) -> dict:
    if path is None:
        return copy.deepcopy(DEFAULTS)
    with open(path, "rb") as fh:
        user = tomllib.load(fh)
    cfg = _merge(DEFAULTS, user)
    _adressen_pruefen(cfg, streng)
    typ = cfg["quelle"]["typ"]
    if typ not in ("demo", "sunspec"):
        raise ValueError(f"quelle.typ muss 'demo' oder 'sunspec' sein, nicht {typ!r}")
    for g in cfg["geraet"]:
        if not g.get("typ"):
            raise ValueError(f"[[geraet]] {g.get('name', '?')}: typ fehlt")
    if typ == "sunspec" and not cfg["geraet"] and not cfg["quelle"]["wechselrichter"]["host"]:
        raise ValueError("Für quelle.typ = 'sunspec' fehlt quelle.wechselrichter.host")
    return cfg
