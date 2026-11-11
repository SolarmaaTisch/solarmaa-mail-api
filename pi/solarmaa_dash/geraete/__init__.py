"""Geräte-Adapter: ein einheitliches Leseformat für alle Messgeräte.

Jeder Adapter liefert eine ``Messung``. Vorzeichen:
  w            Leistung am Messpunkt. Netz/Einheit: + Bezug. PV: Produktion. Speicher: + Entladen.
  import_wh    Zählerstand Bezug
  export_wh    Zählerstand Rückspeisung / Einspeisung
  soc          Ladezustand in Prozent (nur Speicher)
  pv_w, grid_w, battery_w   nur bei Geräten mit Rolle "anlage", die alles auf einmal liefern

Unterstützte Typen (config: ``typ``):
  sunspec         Wechselrichter, Zähler, Speicher mit SunSpec über Modbus TCP
  fronius         Fronius Solar API (HTTP), auch ohne Modbus
  shelly          Shelly Gen1 bis Gen4 (Pro 3EM, EM, 3EM, Plus PM, Pro 4PM, ...)
  smartme_cloud   smart-me Zähler über die smart-me Cloud-API
  mystrom         myStrom Switch (Zwischenstecker), lokal
  goe             go-e Charger (Wallbox), lokale HTTP-API v2
  easee           Easee Wallbox über die Easee-Cloud
  modbus_profil   beliebiges Modbus-TCP-Gerät mit Registerprofil (pi/solarmaa_dash/profile/*.toml),
                  z. B. Huawei SUN2000, smart-me lokal
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Messung:
    w: float | None = None
    import_wh: float | None = None
    export_wh: float | None = None
    soc: float | None = None
    pv_w: float | None = None
    grid_w: float | None = None
    battery_w: float | None = None
    extra: dict = field(default_factory=dict)


class GeraetFehler(Exception):
    """Gerät nicht erreichbar oder liefert unbrauchbare Daten."""


def vorzeichen(cfg: dict) -> int:
    return -1 if int(cfg.get("vorzeichen", 1)) < 0 else 1


def drehen(m: Messung, sign: int) -> Messung:
    """Vorzeichen umkehren (Messzange verkehrt herum / anderer Bezugspunkt)."""
    if sign < 0:
        if m.w is not None:
            m.w = -m.w
        m.import_wh, m.export_wh = m.export_wh, m.import_wh
    return m


def erstelle(cfg: dict, rolle: str):
    """Fabrik: erzeugt den passenden Adapter aus einem Konfigurationsblock."""
    typ = cfg.get("typ", "")
    if typ == "sunspec":
        from .sunspec_geraet import SunSpecGeraet
        return SunSpecGeraet(cfg, rolle)
    if typ == "fronius":
        from .fronius import Fronius
        return Fronius(cfg)
    if typ == "shelly":
        from .shelly import Shelly
        return Shelly(cfg)
    if typ == "smartme_cloud":
        from .smartme import SmartMeCloudZaehler
        return SmartMeCloudZaehler(cfg)
    if typ == "mystrom":
        from .mystrom import MyStrom
        return MyStrom(cfg)
    if typ == "goe":
        from .goe import GoE
        return GoE(cfg)
    if typ == "easee":
        from .easee import Easee
        return Easee(cfg)
    if typ == "modbus_profil":
        from .modbus_profil import ModbusProfilGeraet
        return ModbusProfilGeraet(cfg)
    raise ValueError(f"Unbekannter Gerätetyp {typ!r} (erlaubt: sunspec, fronius, shelly, smartme_cloud, mystrom, goe, easee, modbus_profil)")
