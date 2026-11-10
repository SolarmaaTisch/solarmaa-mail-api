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

    def to_dict(self) -> dict:
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in asdict(self).items()}


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
                        self.pv_wh, self.imp_wh, self.exp_wh)


# ---------------------------------------------------------------------------
ROLLEN = ("pv", "netz", "speicher", "anlage")


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
            self.geraete.append((g, erstelle(g, g["rolle"])))
        self.status: dict[str, str] = {}

    def read(self) -> Snapshot:
        now = time.time()
        pv = grid = bat = soc = None
        pv_wh = imp = exp = None

        def add(a, b):
            return b if a is None else (a if b is None else a + b)

        for g, dev in self.geraete:
            name = g.get("name", g["typ"])
            try:
                m = dev.lesen()
                self.status[name] = "ok"
            except Exception as e:
                self.status[name] = str(e)
                if g.get("optional") or g["rolle"] == "speicher":
                    continue
                raise type(e)(f"{name}: {e}") from e
            r = g["rolle"]
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
        return Snapshot(now, pv or 0.0, grid, load, bat, soc, pv_wh, imp, exp)


def make_source(cfg: dict):
    if not cfg.get("geraet") and cfg["quelle"]["typ"] == "demo":
        return DemoSource(kwp=float(cfg["anlage"]["kwp"]))
    return AnlagenQuelle(cfg)
