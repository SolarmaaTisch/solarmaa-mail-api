"""Fronius über die Solar API v1 (HTTP, im Datamanager / GEN24 eingebaut).

GET /solar_api/v1/GetPowerFlowRealtimeData.fcgi
  Body.Data.Site.P_PV    PV-Leistung (W), null im Standby
  Body.Data.Site.P_Grid  Netz: + Bezug, − Einspeisung
  Body.Data.Site.P_Akku  Batterie (Vorzeichen bei Inbetriebnahme prüfen, siehe akku_vorzeichen)
  Body.Data.Inverters.<n>.SOC   Ladezustand, falls Batterie

Rolle im Config: "anlage" (liefert PV, Netz und Batterie auf einmal).
"""
from __future__ import annotations

from . import GeraetFehler, Messung
from .http import get_json


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


class Fronius:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]
        self.akku_sign = -1 if int(cfg.get("akku_vorzeichen", 1)) < 0 else 1

    def lesen(self) -> Messung:
        j = get_json(f"http://{self.host}/solar_api/v1/GetPowerFlowRealtimeData.fcgi")
        try:
            data = j["Body"]["Data"]
            site = data["Site"]
        except (KeyError, TypeError) as e:
            raise GeraetFehler(f"Fronius {self.host}: unerwartete Antwort") from e
        pv = _num(site.get("P_PV")) or 0.0
        grid = _num(site.get("P_Grid"))
        akku = _num(site.get("P_Akku"))
        soc = None
        for inv in (data.get("Inverters") or {}).values():
            if isinstance(inv, dict) and _num(inv.get("SOC")) is not None:
                soc = _num(inv.get("SOC"))
                break
        return Messung(pv_w=max(pv, 0.0), grid_w=grid,
                       battery_w=None if akku is None else self.akku_sign * akku, soc=soc,
                       import_wh=None, export_wh=None,
                       extra={"pv_wh_total": _num(site.get("E_Total"))})
