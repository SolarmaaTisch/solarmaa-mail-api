"""myStrom Switch (Zwischenstecker), lokal über HTTP – ohne Cloud.

GET /report → {"power": W, "relay": bool, "Ws": Mittelwert seit letzter Abfrage,
               "temperature": °C, "energy_since_boot": Ws, "boot_id": "..."}
Feldnamen aus der myStrom-REST-Doku bzw. der Bibliothek python-mystrom. Ältere Firmware
liefert nur power und relay.

Einsatz: als «verbraucher» (Waschmaschine, Boiler, Kühlschrank …), als «pv» für ein
Balkonkraftwerk am Stecker, oder als Zähler einer Einheit im Haus.
"""
from __future__ import annotations

from . import GeraetFehler, Messung, drehen, vorzeichen
from .http import get_json


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class MyStrom:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]
        self.sign = vorzeichen(cfg)
        self.token = cfg.get("token") or None  # nur falls im myStrom-Gerät ein API-Token gesetzt ist

    def lesen(self) -> Messung:
        hdr = {"Token": self.token} if self.token else None
        r = get_json(f"http://{self.host}/report", headers=hdr)
        if not isinstance(r, dict) or "power" not in r:
            raise GeraetFehler(f"myStrom {self.host}: unerwartete Antwort")
        ws = _num(r.get("energy_since_boot"))
        m = Messung(w=_num(r.get("power")), import_wh=None if ws is None else ws / 3600.0,
                    extra={"relais": r.get("relay"), "temperatur": _num(r.get("temperature")),
                           "boot_id": r.get("boot_id")})
        return drehen(m, self.sign)
