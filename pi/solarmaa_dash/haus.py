"""Haus / ZEV: Einheiten (Wohnungen, Gewerbe, Allgemein) mit ihren Zählern.

Liest alle Einheiten-Zähler (smart-me, Shelly, SunSpec, Modbus-Profil), verteilt den selbst
genutzten Solarstrom anteilig auf die Einheiten und speichert Tageswerte pro Einheit.

Die Solar-Verteilung hier ist eine vereinfachte Live-Rechnung (Anteil am momentanen Verbrauch).
Massgebend für Rechnungen bleibt die Abrechnung in smart-me.
"""
from __future__ import annotations

import logging
import math
import threading
import time

from .geraete import Messung, erstelle
from .store import day_key

log = logging.getLogger("solarmaa_dash")
MAX_GAP_S = 600
TYPEN = ("wohnung", "gewerbe", "allgemein", "technik")


class DemoZaehler:
    """Simulierter Wohnungszähler für Demo und Werkstatt."""

    def __init__(self, seed: int, grundlast: float):
        self.seed, self.grund = seed, grundlast
        self.imp = 1_500_000 + seed * 137_000.0
        self.last = None

    def lesen(self) -> Messung:
        now = time.time()
        lt = time.localtime(now)
        h = lt.tm_hour + lt.tm_min / 60
        s = self.seed

        def bump(c, w, a):
            return a * math.exp(-((h - c) / w) ** 2)

        w = (self.grund + 40 * math.sin(now / (200 + 30 * s))
             + bump(7 + 0.3 * (s % 3), 0.5, 700 + 90 * s) + bump(12.3, 0.7, 500 + 60 * (s % 4))
             + bump(18.5 + 0.4 * (s % 2), 1.0, 900 + 120 * (s % 5)))
        if self.last is not None:
            self.imp += w * min(now - self.last, 120) / 3600
        self.last = now
        return Messung(w=w, import_wh=self.imp, export_wh=0.0)


class Haus:
    def __init__(self, cfg: dict, store):
        h = cfg["haus"]
        self.cfg = h
        self.store = store
        self.name = h.get("name", "Liegenschaft")
        self.geschosse = list(h.get("geschosse") or ["EG"])
        self.intervall = float(h.get("intervall_s", 30))
        self.demo = bool(h.get("demo", False))
        self.einheiten: list[dict] = []
        self.zaehler: dict[str, dict] = {}  # zaehler-id -> {cfg, geraet, einheit, letzte, fehler, zeit}
        seed = 0
        for e in h.get("einheit", []):
            if not e.get("id") or not e.get("name"):
                raise ValueError("[[haus.einheit]] braucht id und name")
            if e.get("typ", "wohnung") not in TYPEN:
                raise ValueError(f"Einheit {e['id']}: typ muss eine von {', '.join(TYPEN)} sein")
            if e.get("geschoss", self.geschosse[0]) not in self.geschosse:
                raise ValueError(f"Einheit {e['id']}: geschoss {e.get('geschoss')!r} fehlt in haus.geschosse")
            ein = {"id": e["id"], "name": e["name"], "geschoss": e.get("geschoss", self.geschosse[0]),
                   "typ": e.get("typ", "wohnung"), "flaeche_m2": e.get("flaeche_m2"),
                   "smartme_ordner": e.get("smartme_ordner", ""), "zaehler": []}
            for z in e.get("zaehler", []):
                zid = z.get("id") or f"{e['id']}-{len(ein['zaehler']) + 1}"
                if zid in self.zaehler:
                    raise ValueError(f"Zähler-ID {zid} doppelt")
                zc = {**z, "_smartme": cfg.get("smartme", {}), "_easee": cfg.get("easee", {})}
                seed += 1
                dev = DemoZaehler(seed, 150 + 40 * (seed % 4)) if self.demo else erstelle(zc, "einheit")
                self.zaehler[zid] = {"id": zid, "name": z.get("name", "Zähler"), "typ": z.get("typ", "demo"),
                                     "seriennummer": z.get("seriennummer", ""), "einheit": e["id"],
                                     "geraet": dev, "letzte": None, "fehler": None, "zeit": None}
                ein["zaehler"].append(zid)
            self.einheiten.append(ein)
        self.lock = threading.Lock()
        self._last_ts: float | None = None
        self._init_db()

    # -- Datenbank --------------------------------------------------------
    def _init_db(self):
        with self.store.lock:
            self.store.db.execute(
                "CREATE TABLE IF NOT EXISTS einheit_tage (day TEXT, einheit TEXT, load_wh REAL NOT NULL DEFAULT 0, "
                "solar_wh REAL NOT NULL DEFAULT 0, PRIMARY KEY (day, einheit))")
            self.store.db.commit()

    def _add_energy(self, day: str, werte: dict[str, tuple[float, float]]):
        with self.store.lock:
            for ein, (load, solar) in werte.items():
                self.store.db.execute(
                    "INSERT INTO einheit_tage (day, einheit, load_wh, solar_wh) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(day, einheit) DO UPDATE SET load_wh = load_wh + excluded.load_wh, "
                    "solar_wh = solar_wh + excluded.solar_wh", (day, ein, load, solar))
            self.store.db.commit()

    # -- Abfrage ----------------------------------------------------------
    def tick(self, anlage) -> None:
        """Alle Zähler lesen, Solaranteil verteilen, Energie aufsummieren."""
        now = time.time()
        for z in self.zaehler.values():
            try:
                z["letzte"] = z["geraet"].lesen()
                z["fehler"], z["zeit"] = None, now
            except Exception as e:  # einzelner Zähler darf ausfallen
                if z["fehler"] != str(e):
                    log.warning("Zähler %s: %s", z["id"], e)
                z["fehler"] = str(e)
        lasten = {e["id"]: self.einheit_w(e) for e in self.einheiten}
        summe = sum(max(v or 0.0, 0.0) for v in lasten.values())
        solar_eigen = 0.0
        if anlage is not None and anlage.pv_w:
            einspeisung = max(-(anlage.grid_w or 0.0), 0.0)
            solar_eigen = max(anlage.pv_w - einspeisung - max(-(anlage.battery_w or 0.0), 0.0), 0.0)
        with self.lock:
            self.solar_verteilung = {}
            for eid, w in lasten.items():
                w = max(w or 0.0, 0.0)
                self.solar_verteilung[eid] = min(w, solar_eigen * w / summe) if summe > 0 else 0.0
            dt = None if self._last_ts is None else now - self._last_ts
            self._last_ts = now
            verteilung = dict(self.solar_verteilung)
        if dt and 0 < dt <= MAX_GAP_S:
            h = dt / 3600
            self._add_energy(day_key(now), {eid: (max(lasten[eid] or 0.0, 0.0) * h, verteilung[eid] * h)
                                            for eid in lasten})

    def einheit_w(self, e: dict) -> float | None:
        werte = [self.zaehler[z]["letzte"].w for z in e["zaehler"]
                 if self.zaehler[z]["letzte"] is not None and self.zaehler[z]["letzte"].w is not None]
        return sum(werte) if werte else None

    def einheit(self, eid: str) -> dict | None:
        return next((e for e in self.einheiten if e["id"] == eid), None)

    def zaehler_info(self, zid: str) -> dict:
        z = self.zaehler[zid]
        m: Messung | None = z["letzte"]
        return {"id": z["id"], "name": z["name"], "typ": z["typ"], "seriennummer": z["seriennummer"],
                "w": None if m is None or m.w is None else round(m.w, 1),
                "import_kwh": None if m is None or m.import_wh is None else round(m.import_wh / 1000, 2),
                "export_kwh": None if m is None or m.export_wh is None else round(m.export_wh / 1000, 2),
                "fehler": z["fehler"], "zeit": z["zeit"]}

    def monate(self, eid: str, jahr: int) -> list[dict]:
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT substr(day, 1, 7) AS m, SUM(load_wh), SUM(solar_wh) FROM einheit_tage "
                "WHERE einheit = ? AND day LIKE ? GROUP BY m ORDER BY m", (eid, f"{jahr}-%")).fetchall()
        return [{"monat": r[0], "verbrauch_kwh": round(r[1] / 1000, 1), "solar_kwh": round(r[2] / 1000, 1),
                 "solar_pct": round(100 * r[2] / r[1]) if r[1] > 50 else None} for r in rows]

    def heute(self, eid: str) -> dict:
        with self.store.lock:
            r = self.store.db.execute("SELECT load_wh, solar_wh FROM einheit_tage WHERE day = ? AND einheit = ?",
                                      (day_key(time.time()), eid)).fetchone()
        load, solar = (r or (0.0, 0.0))
        return {"verbrauch_kwh": round(load / 1000, 2), "solar_kwh": round(solar / 1000, 2),
                "solar_pct": round(100 * solar / load) if load > 50 else None}

    def demo_verlauf(self) -> None:
        """Füllt im Demo-Modus 13 Monate Beispielwerte, damit die Ansicht nicht leer ist."""
        if not self.demo:
            return
        with self.store.lock:
            n = self.store.db.execute("SELECT COUNT(*) FROM einheit_tage").fetchone()[0]
        if n > 30:
            return
        import datetime as dt
        heute = dt.date.today()
        rows = []
        for i, e in enumerate(self.einheiten):
            if e["typ"] == "technik":
                continue
            for d in range(1, 396):
                tag = heute - dt.timedelta(days=d)
                saison = 0.5 - 0.5 * math.cos((tag.timetuple().tm_yday - 10) / 365 * 2 * math.pi)
                load = (5200 + 900 * (i % 4)) * (1.25 - 0.4 * saison) * (0.85 + 0.3 * ((d * 7 + i) % 10) / 10)
                if e["typ"] == "allgemein":
                    load *= 0.45
                rows.append((tag.isoformat(), e["id"], load, load * (0.12 + 0.43 * saison)))
        with self.store.lock:
            self.store.db.executemany("INSERT OR IGNORE INTO einheit_tage VALUES (?, ?, ?, ?)", rows)
            self.store.db.commit()
