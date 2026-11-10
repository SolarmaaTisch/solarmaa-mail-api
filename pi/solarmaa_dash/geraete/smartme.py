"""smart-me Zähler über die smart-me Cloud-API.

Base-URL: https://api.smart-me.com, Auth: ``Authorization: ApiKey <key>`` (empfohlen)
oder Basic-Auth mit Benutzer/Passwort. Gewerbliche Nutzung und höhere Abfrageraten
brauchen laut smart-me eine Professional-Lizenz.

Ein einziger Aufruf GET /Devices liefert alle Zähler des Kontos. Er wird zwischengespeichert,
damit 30 Zähler nicht 30 Abfragen auslösen.

Feldnamen (Gross-/Kleinschreibung egal): Id, Name, Serial, ActivePower, ActivePowerUnit,
CounterReading, CounterReadingImport, CounterReadingExport, CounterReadingUnit.
Bei der Inbetriebnahme mit ``python3 -m solarmaa_dash smartme-liste`` prüfen.
"""
from __future__ import annotations

import base64
import threading
import time

from . import GeraetFehler, Messung, drehen, vorzeichen
from .http import get_json

_KONTEN: dict[tuple, "SmartMeKonto"] = {}
_LOCK = threading.Lock()


def _ci(d: dict, *names):
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] is not None:
            return low[n.lower()]
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SmartMeKonto:
    """Ein smart-me-Konto (API-Key oder Login) mit Zwischenspeicher für /Devices."""

    def __init__(self, basis_url: str, api_key: str | None, benutzer: str | None, passwort: str | None,
                 max_alter_s: float = 30.0):
        self.basis_url = basis_url.rstrip("/")
        if api_key:
            self.headers = {"Authorization": f"ApiKey {api_key}"}
        elif benutzer and passwort:
            tok = base64.b64encode(f"{benutzer}:{passwort}".encode()).decode()
            self.headers = {"Authorization": f"Basic {tok}"}
        else:
            raise ValueError("smart-me: api_key oder benutzer/passwort in [smartme] eintragen")
        self.max_alter_s = max_alter_s
        self._geraete: list[dict] = []
        self._zeit = 0.0
        self._lock = threading.Lock()

    def geraete(self) -> list[dict]:
        with self._lock:
            if time.time() - self._zeit > self.max_alter_s:
                data = get_json(f"{self.basis_url}/Devices", timeout=10, headers=self.headers)
                if not isinstance(data, list):
                    raise GeraetFehler("smart-me: /Devices liefert keine Liste")
                self._geraete, self._zeit = data, time.time()
            return self._geraete

    def finde(self, smartme_id: str | None, seriennummer: str | None) -> dict:
        for d in self.geraete():
            if smartme_id and str(_ci(d, "Id")).lower() == str(smartme_id).lower():
                return d
            if seriennummer and str(_ci(d, "Serial")) == str(seriennummer):
                return d
        raise GeraetFehler(f"smart-me: Zähler {smartme_id or seriennummer} nicht im Konto gefunden")


def konto(cfg_smartme: dict) -> SmartMeKonto:
    key = (cfg_smartme.get("basis_url"), cfg_smartme.get("api_key"), cfg_smartme.get("benutzer"))
    with _LOCK:
        if key not in _KONTEN:
            _KONTEN[key] = SmartMeKonto(cfg_smartme.get("basis_url", "https://api.smart-me.com"),
                                        cfg_smartme.get("api_key"), cfg_smartme.get("benutzer"),
                                        cfg_smartme.get("passwort"),
                                        float(cfg_smartme.get("cache_s", 30)))
        return _KONTEN[key]


def zu_messung(d: dict) -> Messung:
    pu = str(_ci(d, "ActivePowerUnit") or "kW").lower()
    cu = str(_ci(d, "CounterReadingUnit") or "kWh").lower()
    pf = 1.0 if pu == "w" else 1000.0
    cf = 1.0 if cu == "wh" else 1000.0
    p = _num(_ci(d, "ActivePower"))
    imp = _num(_ci(d, "CounterReadingImport"))
    if imp is None:
        imp = _num(_ci(d, "CounterReading"))
    exp = _num(_ci(d, "CounterReadingExport"))
    return Messung(w=None if p is None else p * pf,
                   import_wh=None if imp is None else imp * cf,
                   export_wh=None if exp is None else exp * cf,
                   extra={"serial": _ci(d, "Serial"), "name": _ci(d, "Name"), "id": _ci(d, "Id")})


class SmartMeCloudZaehler:
    def __init__(self, cfg: dict):
        self.cfg_smartme = cfg["_smartme"]  # vom Aufrufer eingefügt: Abschnitt [smartme]
        self.smartme_id = cfg.get("smartme_id")
        self.seriennummer = cfg.get("seriennummer")
        if not (self.smartme_id or self.seriennummer):
            raise ValueError("smart-me-Zähler braucht smartme_id oder seriennummer")
        self.sign = vorzeichen(cfg)

    def lesen(self) -> Messung:
        d = konto(self.cfg_smartme).finde(self.smartme_id, self.seriennummer)
        return drehen(zu_messung(d), self.sign)
