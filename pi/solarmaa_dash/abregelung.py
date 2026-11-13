"""Abregelung bei negativen Day-Ahead-Preisen: Einspeisung auf null, Eigenverbrauch bleibt.

Modi ([abregelung] modus):
  aus         nichts tun
  simulation  rechnen und protokollieren, aber nichts an die Wechselrichter schreiben
  test        nur in den Testfenstern schreiben (Preise werden ignoriert)
  auto        bei Preis < schwelle schreiben (Testfenster gelten zusätzlich)

Regler: Ziel ist ein kleiner Netzbezug (puffer_w), damit sicher nichts eingespeist wird.
  neue_grenze = alte_grenze + verstaerkung × (netz_w − puffer_w)
Die Gesamtgrenze wird nach Nennleistung auf die Wechselrichter verteilt.

Ausfallverhalten ([abregelung] fallback), wenn während negativer Preise der Netzzähler
länger als fallback_nach_s (Standard 120 s) keine Daten liefert oder der Dienst beendet wird:
  halten       letzte Grenze beibehalten (Standard)
  einspeisung  alle Wechselrichter auf Nennleistung (Einspeisung zum negativen Preis möglich)
  aus          alle Wechselrichter auf 0 W (auch kein Eigenverbrauch aus PV)
Bis zum Ablauf der Wartezeit wird die letzte Grenze gehalten. In jedem Fall wird alarmiert
(Lampe, Push, E-Mail, siehe benachrichtigung.py). Endet der negative Zeitraum, wird normal
freigegeben – dafür braucht es keinen Zähler.

Neustart: Der Zustand (begrenzt ja/nein) steht in abregelung-zustand.json. Nach jedem Start wird bei
positivem Preis einmal auf Nennleistung geschrieben, falls die Wechselrichter begrenzt sein könnten;
fehlgeschlagene Freigaben werden bei jedem Schritt wiederholt und alarmiert.
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
import json
from collections import deque
from pathlib import Path

from .geraete.modbus_profil import profil_pfad
from .modbus import ModbusError, ModbusTcpClient

log = logging.getLogger("solarmaa_dash")
MODI = ("aus", "simulation", "test", "auto")
FALLBACKS = ("halten", "einspeisung", "aus")


def lade_steuerprofil(name: str) -> dict:
    with open(profil_pfad(name), "rb") as fh:    # nur mitgelieferte Profile, nie ein Pfad aus der Konfiguration
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
            reg = self.st.get("freigabe_register")
            if reg is not None:
                # Vor jedem Schreiben prüfen: Der Wechselrichter kann das Register zurücksetzen
                # (z. B. nach der Nacht), und eine fremde Steuerung darf nicht überschrieben werden.
                ist = self.client.read_holding(int(reg), 1)[0]
                soll = int(self.st["freigabe_wert"])
                if ist != soll:
                    erlaubt = [int(x) for x in self.st.get("freigabe_ueberschreibbar", [0])]
                    if ist not in erlaubt:
                        raise ModbusError(f"Leistungsbegrenzung steht auf Modus {ist} (andere Steuerung aktiv, "
                                          "z. B. Zähler/Netzbetreiber) und wird nicht überschrieben")
                    self.client.write_registers(int(reg), [soll])
            self.client.write_registers(int(self.st["limit_register"]), self._werte(w))
            self.letzte_w, self.letzte_zeit, self.fehler = w, time.time(), None
        except ModbusError as e:
            self.fehler = str(e)
            self.client.close()
            raise


class Abregelung:
    def __init__(self, cfg: dict, preise, store=None, alarme=None):
        a = cfg["abregelung"]
        from .benachrichtigung import KeineAlarme
        self.alarme = alarme or KeineAlarme()
        self.fallback = a.get("fallback", "halten")
        if self.fallback not in FALLBACKS:
            raise ValueError(f"abregelung.fallback muss eine von {', '.join(FALLBACKS)} sein")
        self.fallback_nach_s = float(a.get("fallback_nach_s", a.get("max_ausfall_s", 120.0)))
        self.fallback_aktiv: str | None = None
        self.modus = a.get("modus", "simulation")
        if self.modus not in MODI:
            raise ValueError(f"abregelung.modus muss eine von {', '.join(MODI)} sein")
        self.preise = preise
        self.store = store
        self.schwelle = float(a.get("schwelle", 0.0))   # gleiche Einheit wie die Preise (ENTSO-E: EUR/MWh)
        self.puffer_w = float(a.get("puffer_w", 500.0))
        self.k = float(a.get("verstaerkung", 0.6))
        self.schritt_s = float(a.get("schritt_s", 10.0))
        self.min_aenderung_w = float(a.get("min_aenderung_w", 1000.0))
        self.wr = [Wechselrichter(w) for w in a.get("wechselrichter", [])]
        if self.modus != "aus" and not self.wr:
            raise ValueError("[[abregelung.wechselrichter]] fehlt")
        preise.pruefe_fenster()
        self.nenn_w = sum(w.nenn_w for w in self.wr)
        self.aktiv = False
        self.grenze_w = self.nenn_w
        self.letzter_schritt = 0.0
        self.letzte_messung = 0.0
        self.grund = ""
        self.preis_jetzt: float | None = None
        self.protokoll: deque = deque(maxlen=200)
        self.lock = threading.Lock()
        self.letzter_netz_w: float | None = None
        pfad = cfg.get("daten", {}).get("pfad", ":memory:")
        self.zustand_datei = None if pfad == ":memory:" else Path(pfad).parent / "abregelung-zustand.json"
        # Nach einem Neustart ist unbekannt, welche Grenze im Wechselrichter steht. War er laut
        # Zustandsdatei begrenzt (oder ist nichts bekannt), wird bei positivem Preis einmal freigegeben.
        self.unbekannt = self._zustand_lesen()
        if store is not None:
            with store.lock:
                store.db.execute("CREATE TABLE IF NOT EXISTS abregel_log (ts INTEGER, ereignis TEXT, preis REAL, "
                                 "grenze_w REAL, netz_w REAL)")
                store.db.commit()

    # -- Hilfen -----------------------------------------------------------
    def _zustand_lesen(self) -> bool:
        """True, wenn die Wechselrichter begrenzt sein könnten (Datei fehlt = unbekannt = True)."""
        if self.zustand_datei is None or not self.zustand_datei.exists():
            return False   # nie gelaufen: wir haben nichts begrenzt
        try:
            return bool(json.loads(self.zustand_datei.read_text()).get("begrenzt", True))
        except (OSError, ValueError):
            return True    # Datei beschädigt: sicherheitshalber freigeben

    def _zustand_schreiben(self) -> None:
        # Begrenzt ist ein Wechselrichter nur, wenn wir ihm tatsächlich weniger als Nennleistung
        # geschrieben haben (oder ein früherer Lauf das getan hat und noch nicht freigegeben ist).
        begrenzt = self.unbekannt or (self.modus != "simulation"
                                      and any(w.letzte_w is not None and w.letzte_w < w.nenn_w for w in self.wr))
        if self.zustand_datei is not None and begrenzt != getattr(self, "_zustand_alt", None):
            try:
                self.zustand_datei.write_text(json.dumps({"begrenzt": begrenzt, "ts": int(time.time())}))
                self._zustand_alt = begrenzt
            except OSError:
                pass

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

    def _verteilen(self, gesamt_w: float, jetzt: float, erzwingen: bool = False, halten: bool = False,
                   lebenszeichen: bool = True, echt: bool = False) -> bool:
        """Verteilt die Gesamtgrenze nach Nennleistung. halten=True schreibt je Wechselrichter den
        zuletzt bestätigten Wert erneut (und nichts, wenn keiner bekannt ist). Gibt True zurück,
        wenn alle Wechselrichter den Sollwert bestätigt haben. Fehlgeschlagene Schreibvorgänge
        werden beim nächsten Schritt automatisch wiederholt, weil letzte_w dann nicht passt."""
        alle_ok = True
        for w in self.wr:
            if halten:
                if w.letzte_w is None:
                    continue
                soll = w.letzte_w
            else:
                soll = gesamt_w * w.nenn_w / self.nenn_w
            neu = w.letzte_w is None or abs(soll - w.letzte_w) >= self.min_aenderung_w \
                or (soll in (0.0, w.nenn_w) and soll != w.letzte_w)
            faellig = lebenszeichen and jetzt - w.letzte_zeit >= 60
            if not (erzwingen or neu or faellig or w.fehler):
                continue
            if self.modus == "simulation" and not echt:
                w.letzte_w, w.letzte_zeit = soll, jetzt
                continue
            try:
                w.schreiben(soll)
                self.alarme.loeschen(f"wr:{w.name}")
            except ModbusError as e:
                alle_ok = False
                log.warning("Abregelung %s: %s", w.name, e)
                self.alarme.setzen(f"wr:{w.name}", f"Wechselrichter {w.name} nimmt keine Leistungsvorgabe an: {e}")
        self._zustand_schreiben()
        return alle_ok

    # -- Ablauf -----------------------------------------------------------
    def tick(self, snap, jetzt: float | None = None) -> None:
        jetzt = jetzt or time.time()
        with self.lock:
            if jetzt - self.letzter_schritt < self.schritt_s:
                return
            self.letzter_schritt = jetzt
            if self.modus == "aus":
                self._sicher_freigeben(jetzt)
                return
            soll, grund = self._soll_abregeln(jetzt)
            self._preise_pruefen(jetzt)
            alter = getattr(snap, "grid_alter_s", 0.0) if snap is not None else None
            messung_da = snap is not None and snap.grid_w is not None and jetzt - snap.ts < 30 and alter < 90
            messung_frisch = messung_da and alter < self.schritt_s
            if messung_da:
                self.letzte_messung = jetzt

            if not soll:
                if self.aktiv:
                    self._log("Ende: Preis wieder positiv" if self.modus != "test" else "Ende Testfenster")
                self.aktiv, self.fallback_aktiv, self.grund, self.grenze_w = False, None, "", self.nenn_w
                self.alarme.loeschen("zaehler")
                self._sicher_freigeben(jetzt)
                return

            if not messung_da:
                ausfall = jetzt - self.letzte_messung if self.letzte_messung else self.fallback_nach_s + 1
                self.alarme.setzen("zaehler", "Netzzähler liefert keine Daten während negativer Strompreise. "
                                   f"Ausfallverhalten: {self.fallback}.")
                if not self.aktiv:
                    self.aktiv = True
                    self._log(f"Start ohne Zählerdaten: {grund}")
                self.grund = f"Netzzähler fehlt – Ausfallverhalten «{self.fallback}»"
                if ausfall < self.fallback_nach_s or self.fallback == "halten":
                    if ausfall >= self.fallback_nach_s and self.fallback_aktiv != "halten":
                        self.fallback_aktiv = "halten"
                        self._log("Ausfallverhalten «halten» angewendet")
                    self._verteilen(self.grenze_w, jetzt, halten=True)     # letzte bestätigte Grenze halten
                else:
                    if self.fallback_aktiv != self.fallback:
                        self.fallback_aktiv = self.fallback
                        self.grenze_w = self.nenn_w if self.fallback == "einspeisung" else 0.0
                        self._log(f"Ausfallverhalten «{self.fallback}» angewendet")
                    self._verteilen(self.grenze_w, jetzt)
                return

            if self.fallback_aktiv:
                self._log("Netzzähler wieder da, Regelung läuft weiter", snap.grid_w)
                self.fallback_aktiv = None
            self.alarme.loeschen("zaehler")
            pv = snap.pv_w if getattr(snap, "pv_gemessen", True) else None
            if not self.aktiv:
                self.aktiv = True
                self.grenze_w = min(self.nenn_w, pv) if pv else self.nenn_w
                self._log(f"Start: {grund}", snap.grid_w)
            self.grund = grund
            if messung_frisch:   # jeden Messwert nur einmal einrechnen (wichtig bei Cloud-Zählern)
                self.grenze_w += self.k * (snap.grid_w - self.puffer_w)
                if pv is not None:
                    # Nicht über die tatsächliche Produktion hinaus aufziehen, sonst dauert es
                    # Minuten, bis die Grenze bei plötzlicher Einspeisung wieder greift.
                    self.grenze_w = min(self.grenze_w, pv + max(0.05 * self.nenn_w, 2 * self.puffer_w))
                self.grenze_w = max(0.0, min(self.nenn_w, self.grenze_w))
            self._verteilen(self.grenze_w, jetzt)

    def _sicher_freigeben(self, jetzt: float) -> None:
        """Ausserhalb negativer Preise: sicherstellen, dass jeder Wechselrichter auf Nennleistung steht.
        Schreibt nach dem Start einmal (falls er begrenzt sein könnte) und wiederholt bei Fehlern."""
        offen = [w for w in self.wr if w.letzte_w != w.nenn_w]
        if not offen:
            return
        if not self.unbekannt and all(w.letzte_w is None for w in offen):
            return   # laut Zustandsdatei nie begrenzt: nichts schreiben
        ok = self._verteilen(self.nenn_w, jetzt, lebenszeichen=False, echt=self.unbekannt)
        if ok and all(w.letzte_w == w.nenn_w for w in self.wr):
            if self.unbekannt:
                self._log("Wechselrichter auf Nennleistung freigegeben")
            self.unbekannt = False
            self._zustand_schreiben()

    def _preise_pruefen(self, jetzt: float) -> None:
        if self.modus == "test" or self.preise.quelle == "keine":
            return
        if self.preise.fehler:
            self.alarme.setzen("preise", self.preise.fehler, "warnung")
        elif self.preise.preis(jetzt, test=False) is None:
            self.alarme.setzen("preise", "Kein Day-Ahead-Preis für die aktuelle Viertelstunde. "
                               "Ohne Preis wird nicht abgeregelt.", "warnung")
        else:
            self.alarme.loeschen("preise")

    def freigeben_alle(self) -> None:
        """Beim Beenden: ausserhalb negativer Preise freigeben, sonst das gewählte Ausfallverhalten."""
        if self.modus in ("aus", "simulation") or not self.aktiv:
            return   # ausserhalb der Abregelung stehen die Wechselrichter bereits auf Nennleistung
        with self.lock:
            if self.fallback == "halten":
                log.warning("Dienst beendet während Abregelung: letzte Grenze bleibt (Ausfallverhalten «halten»)")
                return
            for w in self.wr:
                try:
                    w.schreiben(0.0 if self.fallback == "aus" else w.nenn_w)
                except ModbusError as e:
                    log.error("Vorgabe %s beim Beenden fehlgeschlagen: %s", w.name, e)
            self._zustand_schreiben()

    def status(self) -> dict:
        jetzt = time.time()
        naechste = [z for z in self.preise.zeitreihe(jetzt, jetzt + 36 * 3600) if z["preis"] < self.schwelle]
        return {"modus": self.modus, "aktiv": self.aktiv, "grund": self.grund,
                "fallback": self.fallback, "fallback_aktiv": self.fallback_aktiv,
                "preis": self.preis_jetzt, "schwelle": self.schwelle,
                "grenze_w": round(self.grenze_w), "nenn_w": round(self.nenn_w),
                "naechster_negativ": naechste[0]["start"] if naechste else None,
                "preise_fehler": self.preise.fehler,
                "wechselrichter": [{"name": w.name, "grenze_w": None if w.letzte_w is None else round(w.letzte_w),
                                    "fehler": w.fehler} for w in self.wr]}
