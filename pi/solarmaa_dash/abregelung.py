"""Abregelung bei negativen Day-Ahead-Preisen: Einspeisung auf null, Eigenverbrauch bleibt.

Modi ([abregelung] modus):
  aus         nichts tun
  simulation  rechnen und protokollieren, aber nichts an die Wechselrichter schreiben
  test        nur in den Testfenstern schreiben (Preise werden ignoriert)
  auto        bei Preis < schwelle schreiben (Testfenster gelten zusätzlich)

Regler: Ziel ist ein kleiner Netzbezug (puffer_w), damit sicher nichts eingespeist wird.
  neue_grenze = alte_grenze + verstaerkung × (netz_w − puffer_w)
Die Gesamtgrenze wird nach Nennleistung auf die Wechselrichter verteilt.

Sicherheit:
  - Fällt der Netzzähler länger als max_ausfall_s aus, oder endet der negative Zeitraum,
    werden alle Wechselrichter wieder auf Nennleistung gesetzt (lieber kurz einspeisen als
    tagelang gedrosselt).
  - Beim Beenden des Dienstes wird ebenfalls freigegeben.
  - Geschrieben wird nur bei relevanter Änderung oder als Lebenszeichen alle 60 s, um den
    Wechselrichter nicht mit Schreibbefehlen zu überlasten.
  - Vorgaben des Netzbetreibers (Rundsteuerung) haben im Wechselrichter Vorrang; diese
    Steuerung ersetzt sie nicht.
"""
from __future__ import annotations

import logging
import threading
import time
import tomllib
from collections import deque

from .geraete.modbus_profil import PROFIL_DIR
from .modbus import ModbusError, ModbusTcpClient

log = logging.getLogger("solarmaa_dash")
MODI = ("aus", "simulation", "test", "auto")


def lade_steuerprofil(name: str) -> dict:
    with open(PROFIL_DIR / f"{name}.toml", "rb") as fh:
        prof = tomllib.load(fh)
    st = prof.get("steuerung")
    if not st or "limit_register" not in st:
        raise ValueError(f"Profil {name} hat keinen [steuerung]-Abschnitt")
    prof["steuerung"] = st
    return prof


class Wechselrichter:
    def __init__(self, cfg: dict):
        self.name = cfg.get("name", cfg["host"])
        self.nenn_w = float(cfg["nennleistung_w"])
        self.prof = lade_steuerprofil(cfg.get("profil", "kostal_piko_ci"))
        self.st = self.prof["steuerung"]
        self.wortfolge = self.prof.get("wortfolge", "gross")
        self.client = ModbusTcpClient(cfg["host"], int(cfg.get("port", 502)), int(cfg.get("unit", 1)))
        self.freigegeben = False   # externe Begrenzung aktiviert?
        self.letzte_w: float | None = None
        self.letzte_zeit = 0.0
        self.fehler: str | None = None

    def _werte(self, w: float) -> list[int]:
        v = int(round(max(0.0, min(self.nenn_w, w))))
        if self.st.get("limit_einheit", "W") == "%":
            v = int(round(100 * v / self.nenn_w))
        if self.st.get("limit_typ", "u32") in ("u16", "i16"):
            return [v & 0xFFFF]
        hi, lo = (v >> 16) & 0xFFFF, v & 0xFFFF
        return [hi, lo] if self.wortfolge != "klein" else [lo, hi]

    def schreiben(self, w: float) -> None:
        try:
            if not self.freigegeben and self.st.get("freigabe_register") is not None:
                self.client.write_registers(int(self.st["freigabe_register"]), [int(self.st["freigabe_wert"])])
                self.freigegeben = True
            self.client.write_registers(int(self.st["limit_register"]), self._werte(w))
            self.letzte_w, self.letzte_zeit, self.fehler = w, time.time(), None
        except ModbusError as e:
            self.fehler = str(e)
            self.client.close()
            raise


class Abregelung:
    def __init__(self, cfg: dict, preise, store=None):
        a = cfg["abregelung"]
        self.modus = a.get("modus", "simulation")
        if self.modus not in MODI:
            raise ValueError(f"abregelung.modus muss eine von {', '.join(MODI)} sein")
        self.preise = preise
        self.store = store
        self.schwelle = float(a.get("schwelle", 0.0))   # gleiche Einheit wie die Preise (ENTSO-E: EUR/MWh)
        self.puffer_w = float(a.get("puffer_w", 500.0))
        self.k = float(a.get("verstaerkung", 0.6))
        self.schritt_s = float(a.get("schritt_s", 10.0))
        self.max_ausfall_s = float(a.get("max_ausfall_s", 60.0))
        self.min_aenderung_w = float(a.get("min_aenderung_w", 1000.0))
        self.wr = [Wechselrichter(w) for w in a.get("wechselrichter", [])]
        if self.modus != "aus" and not self.wr:
            raise ValueError("[[abregelung.wechselrichter]] fehlt")
        self.nenn_w = sum(w.nenn_w for w in self.wr)
        self.aktiv = False
        self.grenze_w = self.nenn_w
        self.letzter_schritt = 0.0
        self.letzte_messung = 0.0
        self.grund = ""
        self.preis_jetzt: float | None = None
        self.protokoll: deque = deque(maxlen=200)
        self.lock = threading.Lock()
        if store is not None:
            with store.lock:
                store.db.execute("CREATE TABLE IF NOT EXISTS abregel_log (ts INTEGER, ereignis TEXT, preis REAL, "
                                 "grenze_w REAL, netz_w REAL)")
                store.db.commit()

    # -- Hilfen -----------------------------------------------------------
    def _log(self, ereignis: str, netz_w: float | None = None):
        eintrag = {"ts": time.time(), "ereignis": ereignis, "preis": self.preis_jetzt,
                   "grenze_w": round(self.grenze_w), "netz_w": None if netz_w is None else round(netz_w)}
        self.protokoll.append(eintrag)
        log.info("Abregelung: %s (Preis %s, Grenze %.0f W)", ereignis, self.preis_jetzt, self.grenze_w)
        if self.store is not None:
            with self.store.lock:
                self.store.db.execute("INSERT INTO abregel_log VALUES (?, ?, ?, ?, ?)",
                                      (int(eintrag["ts"]), ereignis, self.preis_jetzt, self.grenze_w, netz_w))
                self.store.db.commit()

    def _soll_abregeln(self, ts: float) -> tuple[bool, str]:
        test = self.preise.im_testfenster(ts)
        p = self.preise.preis(ts, test=False)
        self.preis_jetzt = -100.0 if test and p is None else p
        if self.modus == "test":
            return test, "Testfenster" if test else ""
        if test:
            return True, "Testfenster"
        if p is not None and p < self.schwelle:
            return True, f"Preis {p:.1f} unter Schwelle {self.schwelle:g}"
        return False, ""

    def _verteilen(self, gesamt_w: float, jetzt: float, erzwingen: bool = False) -> None:
        for w in self.wr:
            soll = gesamt_w * w.nenn_w / self.nenn_w
            neu = w.letzte_w is None or abs(soll - w.letzte_w) >= self.min_aenderung_w
            if not (erzwingen or neu or jetzt - w.letzte_zeit >= 60):
                continue
            if self.modus == "simulation":
                w.letzte_w, w.letzte_zeit = soll, jetzt
                continue
            try:
                w.schreiben(soll)
            except ModbusError as e:
                log.warning("Abregelung %s: %s", w.name, e)

    # -- Ablauf -----------------------------------------------------------
    def tick(self, snap, jetzt: float | None = None) -> None:
        if self.modus == "aus":
            return
        jetzt = jetzt or time.time()
        with self.lock:
            if snap is not None and snap.grid_w is not None and jetzt - snap.ts < 30:
                self.letzte_messung = jetzt
            if jetzt - self.letzter_schritt < self.schritt_s:
                return
            self.letzter_schritt = jetzt
            soll, grund = self._soll_abregeln(jetzt)
            messung_ok = jetzt - self.letzte_messung <= self.max_ausfall_s and snap is not None

            if soll and not messung_ok:
                if self.aktiv:
                    self._freigeben(jetzt, "Netzzähler ausgefallen, Freigabe aus Sicherheitsgründen")
                self.grund = "Netzzähler fehlt, keine Abregelung"
                return
            if soll:
                netz = snap.grid_w
                if not self.aktiv:
                    self.aktiv = True
                    self.grenze_w = min(self.nenn_w, snap.pv_w or self.nenn_w)
                    self.grund = grund
                    self._log(f"Start: {grund}", netz)
                self.grenze_w = max(0.0, min(self.nenn_w, self.grenze_w + self.k * (netz - self.puffer_w)))
                self._verteilen(self.grenze_w, jetzt)
            elif self.aktiv:
                self._freigeben(jetzt, "Ende: Preis wieder positiv" if self.modus != "test" else "Ende Testfenster")

    def _freigeben(self, jetzt: float, grund: str) -> None:
        self.aktiv = False
        self.grenze_w = self.nenn_w
        self.grund = ""
        self._verteilen(self.nenn_w, jetzt, erzwingen=True)
        self._log(grund)

    def freigeben_alle(self) -> None:
        """Beim Beenden: alle Wechselrichter auf Nennleistung."""
        if self.modus in ("aus", "simulation"):
            return
        with self.lock:
            for w in self.wr:
                try:
                    w.schreiben(w.nenn_w)
                except ModbusError as e:
                    log.error("Freigabe %s beim Beenden fehlgeschlagen: %s", w.name, e)

    def status(self) -> dict:
        jetzt = time.time()
        naechste = [z for z in self.preise.zeitreihe(jetzt, jetzt + 36 * 3600) if z["preis"] < self.schwelle]
        return {"modus": self.modus, "aktiv": self.aktiv, "grund": self.grund,
                "preis": self.preis_jetzt, "schwelle": self.schwelle,
                "grenze_w": round(self.grenze_w), "nenn_w": round(self.nenn_w),
                "naechster_negativ": naechste[0]["start"] if naechste else None,
                "preise_fehler": self.preise.fehler,
                "wechselrichter": [{"name": w.name, "grenze_w": None if w.letzte_w is None else round(w.letzte_w),
                                    "fehler": w.fehler} for w in self.wr]}
