"""SunSpec-Auslesung über Modbus TCP.

SunSpec ist der herstellerübergreifende Standard, den viele Wechselrichter
(z. B. Fronius, SMA, SolarEdge, Kostal) und Zähler unterstützen. Die
Register-Offsets unten folgen den SunSpec-Modellen 101–103 (Wechselrichter),
124 (Speicher) und 201–204 (Zähler). Bei der Inbetriebnahme mit
``python -m solarmaa_dash scan`` gegen das echte Gerät prüfen.
"""
from __future__ import annotations

from dataclasses import dataclass

from .modbus import ModbusError, ModbusTcpClient

SUNS_MARKER = (0x5375, 0x6E53)  # "SunS"
BASE_ADDRESSES = (40000, 0, 50000)
END_MODEL = 0xFFFF

INVERTER_MODELS = (103, 102, 101, 113, 112, 111)
METER_MODELS = (203, 204, 202, 201, 213, 214, 212, 211)
STORAGE_MODEL = 124


def int16(v: int) -> int | None:
    if v == 0x8000:
        return None  # "nicht implementiert"
    return v - 0x10000 if v & 0x8000 else v


def uint16(v: int) -> int | None:
    return None if v == 0xFFFF else v


def acc32(hi: int, lo: int) -> int | None:
    v = (hi << 16) | lo
    return None if v == 0 else v


def scale(value: int | None, sf: int | None) -> float | None:
    if value is None or sf is None or not -10 <= sf <= 10:
        return None
    return value * (10.0 ** sf)


@dataclass
class Model:
    model_id: int
    start: int  # Adresse des ersten Datenregisters (nach ID und Länge)
    length: int


def discover(client: ModbusTcpClient) -> dict[int, Model]:
    """Sucht den SunSpec-Marker und listet alle Modelle des Geräts."""
    for base in BASE_ADDRESSES:
        try:
            marker = client.read_holding(base, 2)
        except ModbusError:
            continue
        if tuple(marker) == SUNS_MARKER:
            return walk(client, base + 2)
    raise ModbusError("Kein SunSpec-Marker gefunden. Ist SunSpec/Modbus TCP am Gerät aktiviert?")


def walk(client: ModbusTcpClient, addr: int) -> dict[int, Model]:
    models: dict[int, Model] = {}
    for _ in range(100):
        model_id, length = client.read_holding(addr, 2)
        if model_id == END_MODEL or length == 0:
            break
        models.setdefault(model_id, Model(model_id, addr + 2, length))
        addr += 2 + length
    return models


def parse_inverter(regs: list[int], is_float: bool = False) -> dict:
    """Modelle 101–103 (int + Skalierungsfaktor)."""
    if is_float:
        raise NotImplementedError("Float-Modelle 111–113 werden noch nicht unterstützt")
    return {
        "ac_w": scale(int16(regs[12]), int16(regs[13])),
        "ac_wh_total": scale(acc32(regs[22], regs[23]), int16(regs[24])),
        "dc_w": scale(int16(regs[29]), int16(regs[30])) if len(regs) > 30 else None,
        "state": uint16(regs[37]) if len(regs) > 37 else None,
    }


def parse_meter(regs: list[int]) -> dict:
    """Modelle 201–204 (int + Skalierungsfaktor)."""
    wh_sf = int16(regs[52])
    return {
        "w": scale(int16(regs[16]), int16(regs[20])),
        "export_wh_total": scale(acc32(regs[36], regs[37]), wh_sf),
        "import_wh_total": scale(acc32(regs[44], regs[45]), wh_sf),
    }


def parse_storage(regs: list[int]) -> dict:
    """Modell 124: nur Ladezustand (SoC) in Prozent."""
    return {"soc": scale(uint16(regs[6]), int16(regs[20]))}


def describe(models: dict[int, Model]) -> list[str]:
    names = {
        1: "Common (Gerätedaten)",
        101: "Wechselrichter 1-phasig",
        102: "Wechselrichter Split-Phase",
        103: "Wechselrichter 3-phasig",
        111: "Wechselrichter 1-phasig (Float)",
        112: "Wechselrichter Split-Phase (Float)",
        113: "Wechselrichter 3-phasig (Float)",
        124: "Speicher (Basic Storage)",
        160: "MPPT (Strings)",
        201: "Zähler 1-phasig",
        202: "Zähler Split-Phase",
        203: "Zähler 3-phasig (Wye)",
        204: "Zähler 3-phasig (Delta)",
    }
    return [f"Modell {m.model_id:>3} @ {m.start:>5} (Länge {m.length:>3})  {names.get(m.model_id, '')}"
            for m in models.values()]
