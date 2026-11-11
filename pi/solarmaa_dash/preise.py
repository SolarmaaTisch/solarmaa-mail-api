"""Day-Ahead-Strompreise für die Abregelung.

Quellen ([abregelung] preisquelle):
  entsoe  ENTSO-E Transparency Platform, documentType A44, Gebotszone Schweiz 10YCH-SWISSGRIDZ.
          Braucht einen kostenlosen API-Token (Anfrage an transparency@entsoe.eu).
          Raster 15 oder 60 Minuten, je nachdem was ENTSO-E liefert.
  csv     Datei mit Zeilen «Zeitpunkt;Preis» (Preis in EUR/MWh bzw. in der Einheit, mit der auch
          die Schwelle angegeben ist), z. B. vom Netzbetreiber. Zeitpunkt ISO, lokale Zeit.
  keine   Nur Testfenster.

Testfenster ([abregelung] test_fenster): Liste «HH:MM-HH:MM» (jeden Tag) oder
«JJJJ-MM-TT HH:MM-HH:MM» (nur an diesem Tag). In diesen Zeiten gilt der Preis als negativ.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("solarmaa_dash")
ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"
ZONE_CH = "10YCH-SWISSGRIDZ"
TEST_PREIS = -100.0


def _utc(ts: float) -> str:
    return time.strftime("%Y%m%d%H%M", time.gmtime(ts))


def parse_entsoe(xml_text: str) -> dict[int, tuple[int, float]]:
    """A44-Antwort → {start_ts: (ende_ts, preis)}. Fehlende Punkte (A03-Kurve) werden aufgefüllt."""
    root = ET.fromstring(xml_text)
    out: dict[int, tuple[int, float]] = {}
    for period in root.iterfind(".//{*}Period"):
        start = period.find("{*}timeInterval/{*}start").text
        ende = period.find("{*}timeInterval/{*}end").text
        res = period.find("{*}resolution").text
        schritt = {"PT15M": 900, "PT30M": 1800, "PT60M": 3600}.get(res)
        if schritt is None:
            continue
        t0 = int(dt.datetime.strptime(start, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc).timestamp())
        t1 = int(dt.datetime.strptime(ende, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc).timestamp())
        punkte = {}
        for p in period.iterfind(".//{*}Point"):
            pos = int(p.find("{*}position").text)
            preis = p.find("{*}price.amount")
            if preis is not None:
                punkte[pos] = float(preis.text)
        letzter = None
        for i in range((t1 - t0) // schritt):
            pos = i + 1
            if pos in punkte:
                letzter = punkte[pos]
            if letzter is not None:
                a = t0 + i * schritt
                out[a] = (a + schritt, letzter)
    return out


def parse_csv(text: str, raster_s: int = 900) -> dict[int, tuple[int, float]]:
    zeilen = []
    for z in text.splitlines():
        z = z.strip()
        if not z or z.startswith("#") or z.lower().startswith(("zeit", "time", "datum")):
            continue
        teil = z.replace(",", ";", 1).split(";") if ";" not in z else z.split(";")
        if len(teil) < 2:
            continue
        ts_text = teil[0].strip().replace(" ", "T")
        t = dt.datetime.fromisoformat(ts_text)
        ts = int(t.timestamp()) if t.tzinfo is None else int(t.astimezone(dt.timezone.utc).timestamp())
        zeilen.append((ts, float(teil[1].strip().replace("'", ""))))
    zeilen.sort()
    out = {}
    for i, (ts, preis) in enumerate(zeilen):
        ende = zeilen[i + 1][0] if i + 1 < len(zeilen) and zeilen[i + 1][0] - ts <= 3600 else ts + raster_s
        out[ts] = (ende, preis)
    return out


class Preise:
    def __init__(self, cfg_abr: dict, daten_dir: Path | None):
        self.quelle = cfg_abr.get("preisquelle", "entsoe")
        self.token = cfg_abr.get("entsoe_token", "")
        self.zone = cfg_abr.get("zone", ZONE_CH)
        self.csv_pfad = cfg_abr.get("csv_pfad", "")
        self.test_fenster = list(cfg_abr.get("test_fenster", []))
        self.cache_datei = (daten_dir / "preise.json") if daten_dir else None
        self.daten: dict[int, tuple[int, float]] = {}
        self.fehler: str | None = None
        self.stand: float | None = None
        self.lock = threading.Lock()
        self._laden()

    # -- Speicher ---------------------------------------------------------
    def _laden(self):
        if self.cache_datei and self.cache_datei.exists():
            try:
                d = json.loads(self.cache_datei.read_text())
                self.daten = {int(k): (int(v[0]), float(v[1])) for k, v in d.items()}
            except (OSError, ValueError):
                pass

    def _sichern(self):
        if self.cache_datei:
            grenze = time.time() - 3 * 86400
            d = {str(k): v for k, v in self.daten.items() if k > grenze}
            try:
                self.cache_datei.write_text(json.dumps(d))
            except OSError:
                pass

    # -- Abruf ------------------------------------------------------------
    def aktualisieren(self, jetzt: float | None = None) -> None:
        jetzt = jetzt or time.time()
        try:
            if self.quelle == "entsoe":
                if not self.token:
                    raise ValueError("entsoe_token fehlt in [abregelung]")
                heute = dt.datetime.fromtimestamp(jetzt).replace(hour=0, minute=0, second=0, microsecond=0)
                von, bis = heute.timestamp(), (heute + dt.timedelta(days=2)).timestamp()
                q = urllib.parse.urlencode({"securityToken": self.token, "documentType": "A44",
                                            "in_Domain": self.zone, "out_Domain": self.zone,
                                            "periodStart": _utc(von), "periodEnd": _utc(bis)})
                req = urllib.request.Request(f"{ENTSOE_URL}?{q}", headers={"User-Agent": "SolarmaaDash/1.3"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    neu = parse_entsoe(r.read().decode("utf-8"))
            elif self.quelle == "csv":
                neu = parse_csv(Path(self.csv_pfad).read_text(encoding="utf-8-sig"))
            else:
                neu = {}
            with self.lock:
                self.daten.update(neu)
                self.stand, self.fehler = jetzt, None
            self._sichern()
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError, AttributeError) as e:
            self.fehler = f"Preise nicht abrufbar: {getattr(e, 'reason', e)}"
            log.warning(self.fehler)

    def braucht_update(self, jetzt: float | None = None) -> bool:
        jetzt = jetzt or time.time()
        if self.quelle == "keine":
            return False
        if self.stand is None or jetzt - self.stand > 6 * 3600:
            return True
        morgen = (dt.datetime.fromtimestamp(jetzt) + dt.timedelta(days=1)).replace(hour=12, minute=0).timestamp()
        nach_13 = dt.datetime.fromtimestamp(jetzt).hour >= 13
        return nach_13 and self.preis(morgen, test=False) is None and jetzt - self.stand > 1800

    # -- Abfrage ----------------------------------------------------------
    def im_testfenster(self, ts: float) -> bool:
        t = dt.datetime.fromtimestamp(ts)
        for f in self.test_fenster:
            f = f.strip()
            tag = None
            if len(f) > 11 and f[4] == "-":
                tag, f = f[:10], f[11:].strip()
            if tag and tag != t.date().isoformat():
                continue
            a, b = f.split("-")
            ah, am = map(int, a.split(":"))
            bh, bm = map(int, b.split(":"))
            m = t.hour * 60 + t.minute
            if ah * 60 + am <= m < bh * 60 + bm:
                return True
        return False

    def preis(self, ts: float, test: bool = True) -> float | None:
        if test and self.im_testfenster(ts):
            return TEST_PREIS
        with self.lock:
            for start, (ende, p) in self.daten.items():
                if start <= ts < ende:
                    return p
        return None

    def zeitreihe(self, von: float, bis: float) -> list[dict]:
        with self.lock:
            rows = sorted((s, e, p) for s, (e, p) in self.daten.items() if e > von and s < bis)
        return [{"start": s, "ende": e, "preis": p} for s, e, p in rows]
