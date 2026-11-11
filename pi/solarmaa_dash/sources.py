"""Datenquellen: echte Anlage aus beliebigen Geräten (siehe geraete/) oder Demo-Simulation.

Vorzeichen überall gleich:
  pv_w       Produktion (immer >= 0)
  grid_w     Netz: positiv = Bezug, negativ = Einspeisung
  battery_w  Speicher: positiv = Entladen, negativ = Laden
  load_w     Hausverbrauch = pv_w + grid_w (+ battery_w, falls separat gemessen)
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass



@dataclass
class Snapshot:
    ts: float
    pv_w: float | None
    grid_w: float | None
    load_w: float | None
    battery_w: float | None = None
    soc: float | None = None
    pv_wh_total: float | None = None
    import_wh_total: float | None = None
    export_wh_total: float | None = None
    soc_geschaetzt: bool = False
    verbraucher: list | None = None   # einzeln gemessene Verbraucher (Wallbox, Steckdosen), im load_w enthalten

    @property
    def ev_w(self) -> float | None:
        if not self.verbraucher:
            return None
        ws = [v["w"] for v in self.verbraucher if v.get("art") == "wallbox" and v.get("w") is not None]
        return sum(ws) if ws else None

    def to_dict(self) -> dict:
        d = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in asdict(self).items()}
        if self.verbraucher:
            d["verbraucher"] = [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in x.items()}
                                for x in self.verbraucher]
        return d


# ---------------------------------------------------------------------------
class DemoSource:
    """Simuliert eine Anlage mit Speicher. Die App zeigt dabei ein DEMO-Band."""

    name = "demo"

    def __init__(self, kwp: float = 10.0, battery_kwh: float = 10.0):
        self.kwp = kwp
        self.cap_wh = battery_kwh * 1000
        self.soc_wh = self.cap_wh * 0.4
        self.pv_wh = 0.0
        self.imp_wh = 0.0
        self.exp_wh = 0.0
        self._last = None

    @staticmethod
    def _hour(ts: float) -> float:
        lt = time.localtime(ts)
        return lt.tm_hour + lt.tm_min / 60 + lt.tm_sec / 3600

    def pv_at(self, ts: float) -> float:
        h = self._hour(ts)
        lt = time.localtime(ts)
        season = 0.5 - 0.5 * math.cos((lt.tm_yday - 10) / 365 * 2 * math.pi)  # 0 Winter … 1 Sommer
        length = 8.5 + 7 * season
        sun = math.sin(math.pi * (h - (13.1 - length / 2)) / length)
        if sun <= 0:
            return 0.0
        clouds = 0.82 + 0.12 * math.sin(ts / 900) + 0.06 * math.sin(ts / 137)
        return self.kwp * 1000 * (0.28 + 0.52 * season) * sun ** 1.4 * min(1.0, clouds)

    def load_at(self, ts: float) -> float:
        h = self._hour(ts)
        base = 320 + 60 * math.sin(ts / 300)

        def bump(center: float, width: float, height: float) -> float:
            return height * math.exp(-((h - center) / width) ** 2)

        return base + bump(7.2, 0.5, 1400) + bump(12.2, 0.6, 1900) + bump(18.8, 1.0, 2100) + bump(21.5, 0.8, 700)

    def read(self, now: float | None = None) -> Snapshot:
        now = time.time() if now is None else now
        dt = 0.0 if self._last is None else max(0.0, min(now - self._last, 60.0))
        self._last = now
        pv, load = self.pv_at(now), self.load_at(now)
        lt = time.localtime(now)
        h = lt.tm_hour + lt.tm_min / 60
        ev = 3700.0 if 11.0 <= h < 13.5 and pv > 2500 else 0.0  # simuliertes Auto lädt über Mittag
        load += ev
        surplus = pv - load
        if surplus > 0:  # laden
            room = (self.cap_wh * 0.95 - self.soc_wh) * 3600 / dt if dt else surplus
            charge = min(surplus, 3500.0, max(room, 0.0))
            battery = -charge
        else:            # entladen
            avail = (self.soc_wh - self.cap_wh * 0.08) * 3600 / dt if dt else -surplus
            battery = min(-surplus, 3500.0, max(avail, 0.0))
        self.soc_wh -= battery * dt / 3600
        grid = load - pv - battery
        self.pv_wh += pv * dt / 3600
        if grid > 0:
            self.imp_wh += grid * dt / 3600
        else:
            self.exp_wh += -grid * dt / 3600
        return Snapshot(now, pv, grid, load, battery, 100 * self.soc_wh / self.cap_wh,
                        self.pv_wh, self.imp_wh, self.exp_wh,
                        verbraucher=[{"name": "Wallbox (Demo)", "art": "wallbox", "w": ev,
                                      "status": "lädt" if ev else "verbunden", "laedt": ev > 0}])


# ---------------------------------------------------------------------------
ROLLEN = ("pv", "netz", "speicher", "anlage", "verbraucher")


def geraete_aus_config(cfg: dict) -> list[dict]:
    """Liste der Anlagen-Geräte. Neue Form: [[geraet]]. Alte Form [quelle.*] wird umgesetzt."""
    if cfg.get("geraet"):
        out = []
        for g in cfg["geraet"]:
            g = dict(g)
            if g.get("rolle") not in ROLLEN:
                raise ValueError(f"Gerät {g.get('name', '?')}: rolle muss eine von {', '.join(ROLLEN)} sein")
            out.append(g)
        return out
    q = cfg["quelle"]
    out = [{"name": "Wechselrichter", "rolle": "pv", "typ": "sunspec", **q["wechselrichter"]}]
    z = q["zaehler"]
    if z.get("host"):
        out.append({"name": "Netzzähler", "rolle": "netz", "typ": "sunspec",
                    "vorzeichen": z.get("netz_vorzeichen", 1), **z})
    s = q["speicher"]
    if s.get("host"):
        out.append({"name": "Speicher", "rolle": "speicher", "typ": "sunspec", "optional": True, **s})
    return out


class SocSchaetzer:
    """Schätzt den Ladezustand eines Speichers, der nur von aussen gemessen wird
    (z. B. Shelly am Abgang eines AC-gekoppelten Speichers wie Evolium).

    Energiebilanz: Laden × Wirkungsgrad hinein, Entladen ÷ Wirkungsgrad hinaus.
    Selbstkalibrierung:
      voll  = Netz speist ein (Überschuss), Speicher lädt trotzdem nicht, während 5 Minuten
      leer  = Netzbezug, Speicher entlädt trotzdem nicht, während 5 Minuten
    Der Wert wird in einer kleinen Datei gesichert und übersteht Neustarts.
    """

    SCHWELLE_W = 40.0
    NETZ_W = 150.0
    HALTE_S = 300.0

    def __init__(self, kapazitaet_kwh: float, wirkungsgrad: float = 0.92, datei: str | None = None,
                 reserve_pct: float = 0.0):
        self.cap = kapazitaet_kwh * 1000.0
        self.eta = max(0.5, min(1.0, wirkungsgrad))
        self.reserve = reserve_pct
        self.datei = datei
        self.wh = self.cap * 0.5
        self.kalibriert = False
        self._last: float | None = None
        self._voll_seit: float | None = None
        self._leer_seit: float | None = None
        self._laden()

    def _laden(self):
        if not self.datei:
            return
        try:
            import json
            with open(self.datei) as fh:
                d = json.load(fh)
            self.wh = max(0.0, min(self.cap, float(d["wh"])))
            self.kalibriert = bool(d.get("kalibriert"))
        except (OSError, ValueError, KeyError):
            pass

    def _sichern(self):
        if not self.datei:
            return
        try:
            import json
            with open(self.datei, "w") as fh:
                json.dump({"wh": round(self.wh, 1), "kalibriert": self.kalibriert}, fh)
        except OSError:
            pass

    def update(self, ts: float, battery_w: float | None, grid_w: float | None) -> float | None:
        if battery_w is None:
            return None
        dt = 0.0 if self._last is None else ts - self._last
        self._last = ts
        if 0 < dt <= 120:  # Energiebilanz nur über lückenlose Messungen
            h = dt / 3600
            if battery_w < 0:
                self.wh += -battery_w * h * self.eta
            else:
                self.wh -= battery_w * h / self.eta
            self.wh = max(0.0, min(self.cap, self.wh))
        # Kalibrierung: Speicher steht still, obwohl er laden bzw. entladen könnte
        ruhig = abs(battery_w) < self.SCHWELLE_W
        if ruhig and grid_w is not None and grid_w < -self.NETZ_W:
            self._voll_seit = self._voll_seit or ts
            if ts - self._voll_seit >= self.HALTE_S:
                self.wh, self.kalibriert = self.cap, True
        else:
            self._voll_seit = None
        if ruhig and grid_w is not None and grid_w > self.NETZ_W:
            self._leer_seit = self._leer_seit or ts
            if ts - self._leer_seit >= self.HALTE_S:
                self.wh, self.kalibriert = self.cap * self.reserve / 100, True
        else:
            self._leer_seit = None
        if dt <= 0 or int(ts) % 60 < dt:
            self._sichern()
        return 100.0 * self.wh / self.cap


class AnlagenQuelle:
    """Kombiniert beliebig viele Geräte zu einem Anlagen-Messwert.

    Hausverbrauch = PV + Netz + Batterie(Entladen +). Batterieleistung zählt nur, wenn ein Gerät
    sie separat liefert (AC-gekoppelter Speicher, Fronius, Huawei). Bei Hybrid-Wechselrichtern,
    deren AC-Leistung als PV gemessen wird, ist die Batterie schon enthalten (SunSpec Modell 124
    liefert nur den Ladezustand).
    """

    name = "geraete"

    def __init__(self, cfg: dict):
        from .geraete import erstelle
        self.geraete = []
        for g in geraete_aus_config(cfg):
            g.setdefault("_smartme", cfg.get("smartme", {}))
            g.setdefault("_easee", cfg.get("easee", {}))
            self.geraete.append((g, erstelle(g, g["rolle"])))
        self.status: dict[str, str] = {}
        # Ladezustand schätzen, wenn ein Speicher nur gemessen wird (soc_schaetzen = true)
        self.schaetzer = None
        for g, _ in self.geraete:
            if g["rolle"] == "speicher" and g.get("soc_schaetzen"):
                from pathlib import Path
                datei = str(Path(cfg["daten"]["pfad"]).parent / "soc-schaetzung.json") \
                    if cfg["daten"]["pfad"] != ":memory:" else None
                self.schaetzer = SocSchaetzer(float(g.get("kapazitaet_kwh", 10)),
                                              float(g.get("wirkungsgrad", 0.92)), datei,
                                              float(g.get("reserve_pct", 0)))

    def read(self) -> Snapshot:
        now = time.time()
        pv = grid = bat = soc = None
        pv_wh = imp = exp = None
        verbraucher: list[dict] = []

        def add(a, b):
            return b if a is None else (a if b is None else a + b)

        for g, dev in self.geraete:
            name = g.get("name", g["typ"])
            try:
                m = dev.lesen()
                self.status[name] = "ok"
            except Exception as e:
                self.status[name] = str(e)
                if g["rolle"] == "verbraucher":
                    verbraucher.append({"name": name, "art": g.get("art", "geraet"), "w": None, "status": "keine Daten"})
                    continue
                if g.get("optional") or g["rolle"] == "speicher":
                    continue
                raise type(e)(f"{name}: {e}") from e
            r = g["rolle"]
            if r == "verbraucher":
                art = g.get("art") or ("wallbox" if g["typ"] in ("goe", "easee") else "geraet")
                verbraucher.append({"name": name, "art": art, "w": None if m.w is None else max(m.w, 0.0),
                                    **{k: v for k, v in m.extra.items() if k in ("status", "laedt", "sitzung_wh", "relais")}})
                continue
            if r == "pv":
                pv = add(pv, abs(m.w) if m.w is not None else None)
                pv_wh = add(pv_wh, max(m.import_wh or 0.0, m.export_wh or 0.0) or None)
            elif r == "netz":
                grid = add(grid, m.w)
                imp, exp = add(imp, m.import_wh), add(exp, m.export_wh)
            elif r == "speicher":
                bat = add(bat, m.w)
                soc = m.soc if m.soc is not None else soc
            else:  # anlage
                pv, grid, bat = add(pv, m.pv_w), add(grid, m.grid_w), add(bat, m.battery_w)
                soc = m.soc if m.soc is not None else soc
                pv_wh = add(pv_wh, m.extra.get("pv_wh_total"))
        load = None if grid is None else max((pv or 0.0) + grid + (bat or 0.0), 0.0)
        geschaetzt = False
        if self.schaetzer is not None and soc is None:
            soc = self.schaetzer.update(now, bat, grid)
            geschaetzt = soc is not None
        return Snapshot(now, pv or 0.0, grid, load, bat, soc, pv_wh, imp, exp, geschaetzt,
                        verbraucher or None)


def make_source(cfg: dict):
    if not cfg.get("geraet") and cfg["quelle"]["typ"] == "demo":
        return DemoSource(kwp=float(cfg["anlage"]["kwp"]))
    return AnlagenQuelle(cfg)
