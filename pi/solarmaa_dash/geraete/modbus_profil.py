"""Beliebige Modbus-TCP-Geräte über ein Registerprofil (TOML).

So wird jeder Wechselrichter oder Zähler unterstützt, dessen Registerliste bekannt ist,
ohne Programmcode zu ändern: neues Profil in pi/solarmaa_dash/profile/ ablegen.

Profil-Aufbau:
    name = "Hersteller Modell"
    geprueft = false              # true erst nach Test an einer echten Anlage
    funktion = 3                  # 3 = Holding, 4 = Input Register
    wortfolge = "gross"           # "gross" (High-Word zuerst) oder "klein"
    [werte.pv_w]                  # Zielfelder: w, import_wh, export_wh, soc, pv_w, grid_w, battery_w
    register = 32064              # oder Liste [a, b, c]: Werte werden summiert
    typ = "i32"                   # u16, i16, u32, i32, f32
    faktor = 1.0                  # Rohwert * faktor = Zielwert (W, Wh, %)
"""
from __future__ import annotations

import re
import struct
import tomllib
from pathlib import Path

from ..modbus import ModbusError, ModbusTcpClient
from . import GeraetFehler, Messung

PROFIL_DIR = Path(__file__).resolve().parent.parent / "profile"
FELDER = ("w", "import_wh", "export_wh", "soc", "pv_w", "grid_w", "battery_w")
GROESSE = {"u16": 1, "i16": 1, "u32": 2, "i32": 2, "f32": 2}


PROFIL_NAME = re.compile(r"^[a-z0-9_]+$")


def profil_pfad(name) -> Path:
    """Pfad eines mitgelieferten Profils. Nur Namen, keine Pfade: Die Konfiguration (auch die aus
    der Ferne geänderte) darf nie bestimmen, aus welcher Datei Register gelesen oder geschrieben werden."""
    if not isinstance(name, str) or not PROFIL_NAME.match(name):
        raise ValueError(f"Profilname {name!r} ungültig (nur Kleinbuchstaben, Ziffern und _; Profile liegen in {PROFIL_DIR.name}/)")
    p = (PROFIL_DIR / f"{name}.toml").resolve()
    if p.parent != PROFIL_DIR.resolve() or not p.is_file():
        raise ValueError(f"Profil {name!r} gibt es nicht")
    return p


def lade_profil(name: str) -> dict:
    p = profil_pfad(name)
    with open(p, "rb") as fh:
        prof = tomllib.load(fh)
    werte = prof.get("werte") or {}
    if not werte:
        raise ValueError(f"Profil {p.name} enthält noch keine Register ([werte.*] fehlt). "
                         "Registerliste vom Hersteller eintragen.")
    for feld, spec in werte.items():
        if not isinstance(spec.get("register"), (int, list)):
            raise ValueError(f"Profil {p.name}: {feld} braucht register (Zahl oder Liste zum Summieren)")
        if feld not in FELDER:
            raise ValueError(f"Profil {p.name}: unbekanntes Feld {feld!r}")
        if spec.get("typ") not in GROESSE:
            raise ValueError(f"Profil {p.name}: {feld} hat unbekannten typ {spec.get('typ')!r}")
    return prof


def dekodiere(regs: list[int], typ: str, wortfolge: str) -> float | None:
    if typ in ("u16", "i16"):
        v = regs[0]
        if typ == "i16":
            return None if v == 0x8000 else (v - 0x10000 if v & 0x8000 else v)
        return None if v == 0xFFFF else v
    hi, lo = (regs[0], regs[1]) if wortfolge != "klein" else (regs[1], regs[0])
    raw = struct.pack(">HH", hi, lo)
    if typ == "u32":
        v = struct.unpack(">I", raw)[0]
        return None if v == 0xFFFFFFFF else v
    if typ == "i32":
        v = struct.unpack(">i", raw)[0]
        return None if v == -0x80000000 else v
    return struct.unpack(">f", raw)[0]


class ModbusProfilGeraet:
    def __init__(self, cfg: dict):
        self.profil = lade_profil(cfg["profil"])
        self.client = ModbusTcpClient(cfg["host"], int(cfg.get("port", 502)), int(cfg.get("unit", 1)))
        if int(self.profil.get("funktion", 3)) != 3:
            raise ValueError("Zurzeit wird nur Funktion 3 (Holding Register) unterstützt")

    def lesen(self) -> Messung:
        out = Messung()
        wf = self.profil.get("wortfolge", "gross")
        try:
            for feld, spec in self.profil["werte"].items():
                adressen = spec["register"] if isinstance(spec["register"], list) else [spec["register"]]
                summe = None
                for a in adressen:  # Liste = Summe, z. B. mehrere MPPT-Eingänge
                    v = dekodiere(self.client.read_holding(int(a), GROESSE[spec["typ"]]), spec["typ"], wf)
                    if v is not None:
                        summe = (summe or 0.0) + v
                setattr(out, feld, None if summe is None else summe * float(spec.get("faktor", 1.0)))
        except ModbusError as e:
            self.client.close()
            raise GeraetFehler(f"{self.profil.get('name', 'Modbus-Gerät')}: {e}") from e
        return out
