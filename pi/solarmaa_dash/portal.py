"""Verbindung zum Solarmaa-Portal (Supabase).

Der Pi meldet sich von sich aus beim Portal (ausgehende HTTPS-Verbindung, keine Portfreigabe)
und schickt alle intervall_s Sekunden den aktuellen Stand. Fällt das Internet aus, läuft vor Ort
alles weiter; das Portal zeigt die Anlage dann als «offline».

[portal]
url = "https://<projekt>.supabase.co"
schluessel = "sb_publishable_…"     # öffentlicher Schlüssel des Portals
token = "…"                          # geheimer Anlagen-Token, wird im Portal beim Anlegen angezeigt
intervall_s = 60
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
        if self.aktiv and not self.url.startswith("https://") and "127.0.0.1" not in self.url:
            raise ValueError("portal.url muss mit https:// beginnen")
        self.letzter_erfolg: float | None = None
        self.fehler: str | None = None

    def nutzlast(self, state) -> dict:
        now = state.now_payload()
        alarme = [{"text": a.get("text"), "stufe": a.get("stufe"), "seit": a.get("seit")}
                  for a in state.alarme.status()["offen"]][:20]
        return {"version": __version__, "status": now["status"], "stoerung": now.get("stoerung", False),
                "live": now["live"], "heute": now["heute"], "abregelung": now.get("abregelung"), "alarme": alarme}

    def melden(self, state) -> bool:
        body = json.dumps({"p_token": self.token, "p_daten": self.nutzlast(state)}).encode()
        req = urllib.request.Request(f"{self.url}/rest/v1/rpc/pi_melden", data=body, method="POST", headers={
            "apikey": self.schluessel, "Authorization": f"Bearer {self.schluessel}",
            "Content-Type": "application/json", "User-Agent": f"SolarmaaDash/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
            self.letzter_erfolg, self.fehler = time.time(), None
            return True
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            self.fehler = f"Portal antwortet mit HTTP {e.code}" + (f": {detail}" if detail else "") + \
                (" (Token oder Schlüssel prüfen)" if e.code in (401, 403) or "unbekannte Anlage" in detail else "")
        except Exception as e:  # noqa: BLE001 – Portal darf den Pi nie stören
            self.fehler = f"Portal nicht erreichbar: {getattr(e, 'reason', e)}"
        return False

    def status(self) -> dict:
        return {"aktiv": self.aktiv, "letzter_erfolg": self.letzter_erfolg, "fehler": self.fehler,
                "url": self.url if self.aktiv else None}


def portal_loop(portal: Portal, state, stop) -> None:
    letzter_fehler = None
    for _ in range(30):               # erste Meldung erst, wenn ein Messwert da ist
        if state.latest is not None or stop.wait(1):
            break
    while not stop.is_set():
        try:
            ok = portal.melden(state)
            if not ok and portal.fehler != letzter_fehler:
                log.warning(portal.fehler)
            if ok and letzter_fehler:
                log.info("Portal wieder erreichbar")
            letzter_fehler = None if ok else portal.fehler
        except Exception:  # noqa: BLE001
            log.exception("Fehler bei der Portal-Meldung")
        stop.wait(portal.intervall_s)
