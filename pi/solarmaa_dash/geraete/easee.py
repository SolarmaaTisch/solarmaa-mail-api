"""Easee Home / Charge über die Easee-Cloud (Easee bietet keine lokale Schnittstelle).

Login:  POST https://api.easee.com/api/accounts/login {userName, password} → accessToken, refreshToken
Status: GET  /api/chargers/{id}/state → totalPower (kW, laut Integrationen), chargerOpMode,
        sessionEnergy (kWh), lifetimeEnergy (kWh), isOnline
chargerOpMode: 1 kein Auto, 2 wartet auf Start, 3 lädt, 4 fertig, 5 Fehler, 6 bereit
Easee begrenzt die Abfragerate; der Pi fragt höchstens alle 30 s und pausiert bei HTTP 429.
Einheiten (kW/kWh) bei der ersten Anlage mit der Easee-App vergleichen.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from . import GeraetFehler, Messung

BASIS = "https://api.easee.com"
MODUS = {1: "kein Auto", 2: "wartet", 3: "lädt", 4: "fertig", 5: "Fehler", 6: "bereit"}
_KONTEN: dict = {}
_LOCK = threading.Lock()


def _req(method: str, url: str, body: dict | None = None, token: str | None = None, timeout: float = 10.0):
    from ..adressen import pruefe_url
    pruefe_url(url, ("http", "https"), "easee.basis_url")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", "Content-Type": "application/json",
                                          "User-Agent": "SolarmaaDash/1.2",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "null")


class EaseeKonto:
    def __init__(self, benutzer: str, passwort: str, basis: str = BASIS):
        if not (benutzer and passwort):
            raise ValueError("Easee: benutzer und passwort in [easee] eintragen")
        self.benutzer, self.passwort, self.basis = benutzer, passwort, basis.rstrip("/")
        self.token = self.refresh = None
        self.ablauf = 0.0
        self.pause_bis = 0.0
        self.cache: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()

    def _login(self):
        try:
            if self.refresh and self.token:
                d = _req("POST", f"{self.basis}/api/accounts/refresh_token",
                         {"accessToken": self.token, "refreshToken": self.refresh})
            else:
                raise urllib.error.URLError("kein Token")
        except (urllib.error.URLError, ValueError):
            d = _req("POST", f"{self.basis}/api/accounts/login", {"userName": self.benutzer, "password": self.passwort})
        self.token, self.refresh = d["accessToken"], d.get("refreshToken")
        self.ablauf = time.time() + float(d.get("expiresIn", 3600)) - 120

    def zustand(self, charger_id: str, max_alter: float = 30.0) -> dict:
        with self.lock:
            hit = self.cache.get(charger_id)
            if hit and time.time() - hit[0] < max_alter:
                return hit[1]
            if time.time() < self.pause_bis:
                if hit:
                    return hit[1]
                raise GeraetFehler("Easee: Abfragelimit erreicht, Pause")
            try:
                if not self.token or time.time() > self.ablauf:
                    self._login()
                d = _req("GET", f"{self.basis}/api/chargers/{charger_id}/state", token=self.token)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.pause_bis = time.time() + 300
                    raise GeraetFehler("Easee: zu viele Anfragen, Pause 5 Minuten") from e
                if e.code == 401:
                    self.token = None
                raise GeraetFehler(f"Easee: HTTP {e.code}" + (" (Login prüfen)" if e.code in (400, 401) else "")) from e
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                raise GeraetFehler(f"Easee: {getattr(e, 'reason', e)}") from e
            self.cache[charger_id] = (time.time(), d)
            return d


def konto(cfg_easee: dict) -> EaseeKonto:
    from ..adressen import pruefe_url
    try:
        pruefe_url(cfg_easee.get("basis_url", BASIS), ("https",), "easee.basis_url", lokal_http=True)
    except ValueError as e:
        raise GeraetFehler(str(e)) from None
    key = (cfg_easee.get("benutzer"), cfg_easee.get("basis_url", BASIS))
    with _LOCK:
        if key not in _KONTEN:
            _KONTEN[key] = EaseeKonto(cfg_easee.get("benutzer", ""), cfg_easee.get("passwort", ""),
                                      cfg_easee.get("basis_url", BASIS))
        return _KONTEN[key]


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class Easee:
    def __init__(self, cfg: dict):
        self.cfg_easee = cfg["_easee"]
        self.charger_id = cfg.get("charger_id")
        if not self.charger_id:
            raise ValueError("Easee-Wallbox braucht charger_id (Seriennummer, z. B. EH123456)")
        self.max_alter = max(30.0, float(cfg.get("intervall_s", 30)))

    def lesen(self) -> Messung:
        d = konto(self.cfg_easee).zustand(self.charger_id, self.max_alter)
        kw = _num(d.get("totalPower"))
        life = _num(d.get("lifetimeEnergy"))
        sess = _num(d.get("sessionEnergy"))
        mode = d.get("chargerOpMode")
        return Messung(w=None if kw is None else kw * 1000.0, import_wh=None if life is None else life * 1000.0,
                       extra={"status": MODUS.get(mode, "unbekannt") if d.get("isOnline", True) else "offline",
                              "laedt": mode == 3, "verbunden": mode in (2, 3, 4, 6),
                              "sitzung_wh": None if sess is None else sess * 1000.0})
