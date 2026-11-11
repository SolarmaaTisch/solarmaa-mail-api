"""go-e Charger (Gemini, HOMEfix, V3/V4), lokale HTTP-API v2.

Voraussetzung: In der go-e-App die lokale HTTP-API v2 aktivieren.
GET /api/status?filter=nrg,car,wh,eto,amp
  nrg  U L1-L3,N (0–3), I L1-L3 (4–6), P L1,L2,L3,N,Total (7–11), pf (12–15) → Index 11 = Gesamtleistung
  car  0 Unbekannt/Fehler, 1 bereit (kein Auto), 2 lädt, 3 wartet auf Auto, 4 fertig, 5 Fehler
  wh   Energie seit Anstecken (Wh);  eto  Energie gesamt (Wh)
Quelle: github.com/goecharger/go-eCharger-API-v2 (API_KEYS_FIRMWARE/apikeys-en.md, http-en.md).
"""
from __future__ import annotations

from . import GeraetFehler, Messung
from .http import get_json

CAR = {0: "Fehler", 1: "kein Auto", 2: "lädt", 3: "wartet", 4: "fertig", 5: "Fehler"}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class GoE:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]

    def lesen(self) -> Messung:
        r = get_json(f"http://{self.host}/api/status?filter=nrg,car,wh,eto,amp")
        if not isinstance(r, dict) or "nrg" not in r:
            raise GeraetFehler(f"go-e {self.host}: keine Daten. Ist die lokale HTTP-API v2 in der App aktiviert?")
        nrg = r.get("nrg") or []
        w = _num(nrg[11]) if len(nrg) > 11 else None
        car = r.get("car")
        return Messung(w=w, import_wh=_num(r.get("eto")),
                       extra={"status": CAR.get(car, "unbekannt") if car is not None else "Fehler",
                              "laedt": car == 2, "verbunden": car in (2, 3, 4),
                              "sitzung_wh": _num(r.get("wh")), "ampere": _num(r.get("amp"))})
