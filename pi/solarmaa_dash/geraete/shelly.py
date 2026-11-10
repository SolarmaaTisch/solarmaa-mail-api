"""Shelly Energiemessgeräte, lokal über HTTP (kein Cloud-Konto nötig).

Gen2/Gen3/Gen4:  GET /rpc/Shelly.GetStatus, Komponenten em:N / emdata:N (Pro 3EM, 3EM Gen3),
                 em1:N / em1data:N (Pro EM, EM Gen3), pm1:N, switch:N (Plus PM, Pro 4PM, ...)
Gen1:            GET /status, emeters[] (Shelly 3EM, EM) oder meters[] (Shelly 1PM, Plug)

Feldnamen nach Shelly-API-Dokumentation; bei der Inbetriebnahme mit
``python3 -m solarmaa_dash geraet-test`` gegen das echte Gerät prüfen.
"""
from __future__ import annotations

from . import GeraetFehler, Messung, drehen, vorzeichen
from .http import get_json


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


class Shelly:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]
        self.kanal = cfg.get("kanal")          # z. B. "em:0", "switch:1"; leer = automatisch
        self.password = cfg.get("passwort") or None
        self.sign = vorzeichen(cfg)
        self.gen: int | None = None

    def _get(self, path: str):
        return get_json(f"http://{self.host}{path}", user="admin", password=self.password)

    def lesen(self) -> Messung:
        if self.gen is None:
            info = self._get("/shelly")
            self.gen = int(info.get("gen", 1) or 1)
        try:
            m = self._gen2() if self.gen >= 2 else self._gen1()
        except (KeyError, TypeError, IndexError) as e:
            raise GeraetFehler(f"Shelly {self.host}: unerwartete Antwort ({e})") from e
        return drehen(m, self.sign)

    def _gen2(self) -> Messung:
        st = self._get("/rpc/Shelly.GetStatus")
        key = self.kanal or next((k for p in ("em:", "em1:", "pm1:", "switch:")
                                  for k in sorted(st) if k.startswith(p)), None)
        if key is None or key not in st:
            raise GeraetFehler(f"Shelly {self.host}: keine Messkomponente gefunden ({', '.join(sorted(st))})")
        c, idx = st[key], key.split(":")[1]
        if key.startswith("em:"):
            w = _num(c.get("total_act_power"))
            if w is None:
                w = sum(_num(c.get(f"{p}_act_power")) or 0.0 for p in "abc")
            d = st.get(f"emdata:{idx}", {})
            return Messung(w=w, import_wh=_num(d.get("total_act")), export_wh=_num(d.get("total_act_ret")))
        if key.startswith("em1:"):
            d = st.get(f"em1data:{idx}", {})
            return Messung(w=_num(c.get("act_power")), import_wh=_num(d.get("total_act_energy")),
                           export_wh=_num(d.get("total_act_ret_energy")))
        # pm1 / switch
        return Messung(w=_num(c.get("apower")),
                       import_wh=_num((c.get("aenergy") or {}).get("total")),
                       export_wh=_num((c.get("ret_aenergy") or {}).get("total")))

    def _gen1(self) -> Messung:
        st = self._get("/status")
        if st.get("emeters"):
            ems = st["emeters"]
            if self.kanal is not None and str(self.kanal).isdigit():
                ems = [ems[int(self.kanal)]]
            return Messung(w=sum(_num(e.get("power")) or 0.0 for e in ems),
                           import_wh=sum(_num(e.get("total")) or 0.0 for e in ems),
                           export_wh=sum(_num(e.get("total_returned")) or 0.0 for e in ems))
        meters = st.get("meters") or []
        if not meters:
            raise GeraetFehler(f"Shelly {self.host}: Gerät misst keine Leistung")
        mt = meters[int(self.kanal) if str(self.kanal or "").isdigit() else 0]
        total = _num(mt.get("total"))  # Gen1 meters: Wattminuten
        return Messung(w=_num(mt.get("power")), import_wh=None if total is None else total / 60)
