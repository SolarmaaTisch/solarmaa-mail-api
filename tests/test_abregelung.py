"""Tests: Day-Ahead-Preise und Abregelung (Nulleinspeisung) gegen simulierte Wechselrichter."""
import datetime as dt
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "pi"), str(ROOT / "tests")]

from solarmaa_dash import config  # noqa: E402
from solarmaa_dash.abregelung import Abregelung  # noqa: E402
from solarmaa_dash.preise import Preise, parse_csv, parse_entsoe  # noqa: E402
from solarmaa_dash.sources import Snapshot  # noqa: E402
from test_dash import FakeModbus  # noqa: E402

XML = """<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
 <TimeSeries><Period>
  <timeInterval><start>2026-06-21T10:00Z</start><end>2026-06-21T11:00Z</end></timeInterval>
  <resolution>PT15M</resolution>
  <Point><position>1</position><price.amount>12.5</price.amount></Point>
  <Point><position>2</position><price.amount>-3.0</price.amount></Point>
  <Point><position>4</position><price.amount>5.0</price.amount></Point>
 </Period></TimeSeries>
</Publication_MarketDocument>"""


class PreisTests(unittest.TestCase):
    def test_entsoe_15min_mit_luecke(self):
        d = parse_entsoe(XML)
        t0 = int(dt.datetime(2026, 6, 21, 10, 0, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(len(d), 4)
        self.assertEqual(d[t0], (t0 + 900, 12.5))
        self.assertEqual(d[t0 + 1800][1], -3.0)   # Position 3 fehlt → Vorwert
        self.assertEqual(d[t0 + 2700][1], 5.0)

    def test_csv_und_testfenster(self):
        d = parse_csv("Zeit;Preis\n2026-06-21 12:00;-4.2\n2026-06-21 12:15;1.0\n")
        t = int(dt.datetime(2026, 6, 21, 12, 0).timestamp())
        self.assertEqual(d[t], (t + 900, -4.2))
        p = Preise({"preisquelle": "keine", "test_fenster": ["12:00-12:30", "2026-06-22 08:00-09:00"]}, None)
        self.assertTrue(p.im_testfenster(dt.datetime(2026, 6, 21, 12, 10).timestamp()))
        self.assertFalse(p.im_testfenster(dt.datetime(2026, 6, 21, 12, 30).timestamp()))
        self.assertTrue(p.im_testfenster(dt.datetime(2026, 6, 22, 8, 30).timestamp()))
        self.assertFalse(p.im_testfenster(dt.datetime(2026, 6, 23, 8, 30).timestamp()))


class AbregelTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeModbus({1: {}})
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        port = self.srv.server_address[1]
        wr = [{"name": f"WR{i}", "host": "127.0.0.1", "port": port, "unit": 1, "nennleistung_w": 60000}
              for i in range(1, 5)]
        self.cfg = config._merge(config.DEFAULTS, {"abregelung": {
            "modus": "auto", "preisquelle": "keine", "puffer_w": 500, "verstaerkung": 0.6,
            "schritt_s": 10, "min_aenderung_w": 500, "wechselrichter": wr}})
        self.preise = Preise(self.cfg["abregelung"], None)

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()

    def limits(self):
        return [(w[0], (w[2][0] << 16) | w[2][1]) for w in self.srv.writes if w[1] == 12421]

    def test_nulleinspeisung_regelt_und_gibt_frei(self):
        t = time.time()
        self.preise.daten = {int(t) - 60: (int(t) + 3600, -20.0)}
        a = Abregelung(self.cfg, self.preise)
        anlage_pv, haus = 150000.0, 30000.0
        for i in range(30):                          # Anlage reagiert auf die Grenze
            pv = min(anlage_pv, a.grenze_w if a.aktiv else anlage_pv)
            a.tick(Snapshot(t + i * 10, pv, haus - pv, haus), jetzt=t + i * 10)
        self.assertTrue(a.aktiv)
        netz = haus - min(anlage_pv, a.grenze_w)
        self.assertGreater(netz, 0)                  # keine Einspeisung mehr
        self.assertLess(netz, 2000)                  # aber kaum Netzbezug
        freigaben = [w for w in self.srv.writes if w[1] == 12467]
        self.assertEqual(len(freigaben), 4)          # Freigaberegister je WR einmal
        self.assertTrue(all(0 <= v <= 60000 for _, v in self.limits()))
        # Preis wieder positiv → alle auf Nennleistung
        self.preise.daten = {int(t) - 60: (int(t) + 3600, 30.0)}
        a.tick(Snapshot(t + 400, 30000, 0, 30000), jetzt=t + 400)
        self.assertFalse(a.aktiv)
        self.assertEqual([v for _, v in self.limits()[-4:]], [60000] * 4)

    def test_zaehlerausfall_gibt_frei(self):
        t = time.time()
        self.preise.daten = {int(t) - 60: (int(t) + 3600, -5.0)}
        a = Abregelung(self.cfg, self.preise)
        a.tick(Snapshot(t, 100000, -70000, 30000), jetzt=t)
        a.tick(Snapshot(t + 10, 60000, -30000, 30000), jetzt=t + 10)
        self.assertTrue(a.aktiv)
        a.tick(None, jetzt=t + 100)                  # 90 s keine Messung
        self.assertFalse(a.aktiv)
        self.assertEqual([v for _, v in self.limits()[-4:]], [60000] * 4)

    def test_simulation_schreibt_nichts(self):
        t = time.time()
        self.cfg["abregelung"]["modus"] = "simulation"
        self.preise.daten = {int(t) - 60: (int(t) + 3600, -5.0)}
        a = Abregelung(self.cfg, self.preise)
        a.tick(Snapshot(t, 100000, -70000, 30000), jetzt=t)
        self.assertTrue(a.aktiv)
        self.assertLess(a.grenze_w, 100000)
        self.assertEqual(self.srv.writes, [])

    def test_testmodus_ignoriert_preise(self):
        t = time.time()
        self.cfg["abregelung"]["modus"] = "test"
        self.preise.daten = {int(t) - 60: (int(t) + 3600, -50.0)}
        a = Abregelung(self.cfg, self.preise)
        a.tick(Snapshot(t, 100000, -70000, 30000), jetzt=t)
        self.assertFalse(a.aktiv)                    # negativer Preis, aber kein Testfenster


if __name__ == "__main__":
    unittest.main()
