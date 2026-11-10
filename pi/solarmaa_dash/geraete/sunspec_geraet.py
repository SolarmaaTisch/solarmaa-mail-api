"""SunSpec-Gerät als Adapter. Rolle bestimmt, welches Modell gelesen wird:
pv → Wechselrichter (101–103), netz/einheit → Zähler (201–204), speicher → Modell 124 (Ladezustand).
"""
from __future__ import annotations

from .. import sunspec
from ..modbus import ModbusError, ModbusTcpClient
from . import GeraetFehler, Messung, drehen, vorzeichen


class SunSpecGeraet:
    def __init__(self, cfg: dict, rolle: str):
        self.client = ModbusTcpClient(cfg["host"], int(cfg.get("port", 502)), int(cfg.get("unit", 1)))
        self.rolle = rolle
        self.sign = vorzeichen(cfg)
        self.models: dict | None = None

    def _modell(self, ids):
        if self.models is None:
            self.models = sunspec.discover(self.client)
        return next((self.models[i] for i in ids if i in self.models), None)

    def lesen(self) -> Messung:
        try:
            if self.rolle == "pv":
                m = self._modell(sunspec.INVERTER_MODELS)
                if m is None:
                    raise GeraetFehler("kein SunSpec-Wechselrichtermodell (101–103) gefunden")
                inv = sunspec.parse_inverter(self.client.read_many(m.start, m.length), is_float=m.model_id > 110)
                return Messung(w=max(inv["ac_w"] or 0.0, 0.0), export_wh=inv["ac_wh_total"])
            if self.rolle == "speicher":
                m = self._modell((sunspec.STORAGE_MODEL,))
                if m is None:
                    raise GeraetFehler("kein SunSpec-Speichermodell (124) gefunden")
                return Messung(soc=sunspec.parse_storage(self.client.read_many(m.start, m.length))["soc"])
            m = self._modell(sunspec.METER_MODELS)
            if m is None:
                raise GeraetFehler("kein SunSpec-Zählermodell (201–204) gefunden")
            met = sunspec.parse_meter(self.client.read_many(m.start, m.length))
            return drehen(Messung(w=met["w"], import_wh=met["import_wh_total"],
                                  export_wh=met["export_wh_total"]), self.sign)
        except (ModbusError, NotImplementedError) as e:
            self.models = None
            self.client.close()
            raise GeraetFehler(str(e)) from e
