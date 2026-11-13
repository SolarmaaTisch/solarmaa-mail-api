"""Verbindung zum Solarmaa-Portal (Supabase).

Der Pi meldet sich von sich aus beim Portal (ausgehende HTTPS-Verbindung, keine Portfreigabe)
und schickt alle intervall_s Sekunden den aktuellen Stand. Fällt das Internet aus, läuft vor Ort
alles weiter; das Portal zeigt die Anlage dann als «offline».

[portal]
url = "https://<projekt>.supabase.co"
schluessel = "sb_publishable_…"     # öffentlicher Schlüssel des Portals
token = "…"                          # geheimer Anlagen-Token, wird im Portal beim Anlegen angezeigt
intervall_s = 60
fernwartung = false                  # true: Solarmaa darf über das Portal aktualisieren und konfigurieren
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from . import __version__

log = logging.getLogger("solarmaa_dash")


class Portal:
    def __init__(self, cfg: dict):
        p = cfg.get("portal") or {}
        self.url = (p.get("url") or "").rstrip("/")
        self.schluessel = p.get("schluessel") or ""
        self.token = p.get("token") or ""
        self.intervall_s = max(30.0, float(p.get("intervall_s", 60)))
        self.aktiv = bool(self.url and self.schluessel and self.token)
        if self.aktiv:
            # https Pflicht; http nur für einen Testdienst auf diesem Rechner (genau 127.0.0.1/localhost)
            from .adressen import pruefe_url
            pruefe_url(self.url, ("https",), "portal.url", lokal_http=True)
        fw = p.get("fernwartung", False)
        if not isinstance(fw, bool):         # "false" als Text wäre sonst wahr
            raise ValueError("portal.fernwartung muss true oder false sein")
        self.fernwartung = fw
        self.letzter_erfolg: float | None = None
        self.fehler: str | None = None
        self.letzter_befehl: dict | None = None      # von der Fernwartung gesetzt (nur Anzeige)
        self.konfig_hinweis: str | None = None

    def nutzlast(self, state) -> dict:
        now = state.now_payload()
        alarme = [{"text": a.get("text"), "stufe": a.get("stufe"), "seit": a.get("seit")}
                  for a in state.alarme.status()["offen"]][:20]
        return {"version": __version__, "status": now["status"], "stoerung": now.get("stoerung", False),
                "live": now["live"], "heute": now["heute"], "abregelung": now.get("abregelung"), "alarme": alarme,
                "fernwartung": self.fernwartung}

    def rpc(self, fn: str, args: dict) -> tuple[int, object]:
        """Ruft eine Funktion des Portals auf. Liefert (HTTP-Status, Antwort); Status 0 = nicht erreichbar.
        Wirft nie; der Token steht nur im Körper der Anfrage und in keinem Fehlertext."""
        from .adressen import pruefe_url
        try:
            pruefe_url(self.url, ("https",), "portal.url", lokal_http=True)
        except ValueError as e:
            return 0, str(e)
        req = urllib.request.Request(f"{self.url}/rest/v1/rpc/{fn}", data=json.dumps(args).encode(), method="POST",
                                     headers={"apikey": self.schluessel, "Authorization": f"Bearer {self.schluessel}",
                                              "Content-Type": "application/json",
                                              "User-Agent": f"SolarmaaDash/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                roh = r.read()
                return r.status, (json.loads(roh) if roh.strip() else None)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:  # noqa: BLE001
                return e.code, None
        except Exception as e:  # noqa: BLE001 – Portal darf den Pi nie stören
            return 0, str(getattr(e, "reason", e))

    def melden(self, state) -> dict | None:
        """Schickt den Stand. Liefert die Antwort des Portals (mit «befehle») oder None bei Fehler."""
        code, data = self.rpc("pi_melden", {"p_token": self.token, "p_daten": self.nutzlast(state)})
        if 200 <= code < 300:
            self.letzter_erfolg, self.fehler = time.time(), None
            return data if isinstance(data, dict) else {}       # ältere Datenbank: Antwort ohne Befehle
        if code == 0:
            self.fehler = f"Portal nicht erreichbar: {data}"
            return None
        detail = str(data.get("message") or "") if isinstance(data, dict) else ""
        self.fehler = f"Portal antwortet mit HTTP {code}" + (f": {detail}" if detail else "") + \
            (" (Token oder Schlüssel prüfen)" if code in (401, 403) or "unbekannte Anlage" in detail else "")
        return None

    def status(self) -> dict:
        return {"aktiv": self.aktiv, "letzter_erfolg": self.letzter_erfolg, "fehler": self.fehler,
                "url": self.url if self.aktiv else None, "fernwartung": self.fernwartung,
                "letzter_befehl": self.letzter_befehl, "konfig_hinweis": self.konfig_hinweis}


def portal_loop(portal: Portal, state, stop) -> None:
    letzter_fehler = None
    for _ in range(30):               # erste Meldung erst, wenn ein Messwert da ist
        if state.latest is not None or stop.wait(1):
            break
    while not stop.is_set():
        antwort = None
        try:
            antwort = portal.melden(state)
            ok = antwort is not None
            if not ok and portal.fehler != letzter_fehler:
                log.warning(portal.fehler)
            if ok and letzter_fehler:
                log.info("Portal wieder erreichbar")
            letzter_fehler = None if ok else portal.fehler
        except Exception:  # noqa: BLE001
            log.exception("Fehler bei der Portal-Meldung")
        fern = getattr(state, "fernwartung", None)
        if fern is not None:
            try:
                if antwort is not None:
                    fern.verarbeiten(antwort.get("befehle"))
            except Exception:  # noqa: BLE001
                log.exception("Fehler beim Verarbeiten der Fernbefehle")
            try:
                fern.nachlauf()      # auch nach fehlgeschlagener Meldung: Fristen, Quittungen, Konfiguration
            except Exception:  # noqa: BLE001
                log.exception("Fehler im Nachlauf der Fernwartung")
        stop.wait(portal.intervall_s)
