"""whatwatt Go: liest die Kundenschnittstelle des Smart Meters des Netzbetreibers aus.

Lokal über HTTP: GET /api/v1/report (laut whatwatt nur mit Lizenz «Plus» oder höher).
Feldpfade nach der Loxone-Integrationsanleitung von whatwatt:
  report.instantaneous_power.active.positive.total   Bezug (Leistung)
  report.instantaneous_power.active.negative.total   Einspeisung (Leistung)
  report.energy.active.positive.total                Zählerstand Bezug
  report.energy.active.negative.total                Zählerstand Einspeisung
Die Quellen widersprechen sich bei den Einheiten (W/Wh oder kW/kWh). Darum einstellbar:
  leistung_einheit = "kW" | "W", energie_einheit = "kWh" | "Wh"   (Standard kW / kWh)
Bei der Inbetriebnahme mit der Anzeige des Smart Meters vergleichen (geraet-test).

Rolle meist «netz» (Hauptzähler) oder Zähler einer Einheit im Haus.
"""
from __future__ import annotations

from . import GeraetFehler, Messung, drehen, vorzeichen
from .http import get_json


def _pfad(d, *keys):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) else None


class WhatWatt:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]
        self.user = cfg.get("benutzer") or "admin"
        self.password = cfg.get("passwort") or None
        self.pf = 1000.0 if str(cfg.get("leistung_einheit", "kW")).lower() == "kw" else 1.0
        self.ef = 1000.0 if str(cfg.get("energie_einheit", "kWh")).lower() == "kwh" else 1.0
        self.sign = vorzeichen(cfg)

    def lesen(self) -> Messung:
        try:
            j = get_json(f"http://{self.host}/api/v1/report", user=self.user, password=self.password)
        except GeraetFehler as e:
            if "HTTP 403" in str(e) or "HTTP 404" in str(e):
                raise GeraetFehler(f"whatwatt {self.host}: /api/v1/report nicht verfügbar. "
                                   "Ist die Lizenz «Plus» aktiviert?") from e
            raise
        r = j.get("report") if isinstance(j, dict) else None
        if not isinstance(r, dict):
            raise GeraetFehler(f"whatwatt {self.host}: unerwartete Antwort (kein «report»)")
        p_imp = _pfad(r, "instantaneous_power", "active", "positive", "total")
        p_exp = _pfad(r, "instantaneous_power", "active", "negative", "total")
        e_imp = _pfad(r, "energy", "active", "positive", "total")
        e_exp = _pfad(r, "energy", "active", "negative", "total")
        if p_imp is None and p_exp is None:
            raise GeraetFehler(f"whatwatt {self.host}: Bericht enthält keine Leistung. Liefert der Zähler Daten?")
        w = ((p_imp or 0.0) - (p_exp or 0.0)) * self.pf
        m = Messung(w=w, import_wh=None if e_imp is None else e_imp * self.ef,
                    export_wh=None if e_exp is None else e_exp * self.ef)
        return drehen(m, self.sign)
