"""Datenquellen: echte Anlage über SunSpec oder Demo-Simulation.

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

from . import sunspec
from .modbus import ModbusError, ModbusTcpClient


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
class _Device:
    def __init__(self, host: str, port: int, unit: int):
        self.client = ModbusTcpClient(host, port, unit)
        self.models: dict[int, sunspec.Model] | None = None

    def model(self, ids: tuple[int, ...]) -> sunspec.Model | None:
        if self.models is None:
            self.models = sunspec.discover(self.client)
        for mid in ids:
            if mid in self.models:
                return self.models[mid]
        return None

    def block(self, m: sunspec.Model) -> list[int]:
        return self.client.read_many(m.start, m.length)

    def reset(self) -> None:
        self.models = None
        self.client.close()


class SunSpecSource:
    name = "sunspec"

    def __init__(self, cfg: dict):
        q = cfg["quelle"]
        wr = q["wechselrichter"]
        self.inverter = _Device(wr["host"], int(wr["port"]), int(wr["unit"]))
        z = q["zaehler"]
        self.meter = _Device(z["host"], int(z["port"]), int(z["unit"])) if z.get("host") else None
        self.grid_sign = -1 if int(z.get("netz_vorzeichen", 1)) < 0 else 1
        s = q["speicher"]
        self.storage = _Device(s["host"], int(s["port"]), int(s["unit"])) if s.get("host") else None

    def read(self) -> Snapshot:
        now = time.time()
        try:
            m = self.inverter.model(sunspec.INVERTER_MODELS)
            if m is None:
                raise ModbusError("Wechselrichter liefert kein SunSpec-Wechselrichtermodell (101–103)")
            inv = sunspec.parse_inverter(self.inverter.block(m), is_float=m.model_id > 110)
        except Exception:
            self.inverter.reset()
            raise
        pv = max(inv["ac_w"] or 0.0, 0.0)

        grid = imp = exp = None
        if self.meter:
            try:
                mm = self.meter.model(sunspec.METER_MODELS)
                if mm is None:
                    raise ModbusError("Zähler liefert kein SunSpec-Zählermodell (201–204)")
                met = sunspec.parse_meter(self.meter.block(mm))
            except Exception:
                self.meter.reset()
                raise
            grid = None if met["w"] is None else self.grid_sign * met["w"]
            imp, exp = met["import_wh_total"], met["export_wh_total"]
            if self.grid_sign < 0:
                imp, exp = exp, imp

        soc = None
        if self.storage:
            try:
                sm = self.storage.model((sunspec.STORAGE_MODEL,))
                if sm is not None:
                    soc = sunspec.parse_storage(self.storage.block(sm))["soc"]
            except Exception:
                self.storage.reset()  # Speicher ist optional: Fehler nicht weiterreichen

        load = None if grid is None else max(pv + grid, 0.0)
        return Snapshot(now, pv, grid, load, None, soc, inv["ac_wh_total"], imp, exp)


def make_source(cfg: dict):
    if cfg["quelle"]["typ"] == "sunspec":
        return SunSpecSource(cfg)
    return DemoSource(kwp=float(cfg["anlage"]["kwp"]))
