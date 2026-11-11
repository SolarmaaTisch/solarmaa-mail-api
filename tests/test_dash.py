"""Tests: python3 -m unittest discover -s tests"""
import json
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))

from solarmaa_dash import config, sunspec  # noqa: E402
from solarmaa_dash.modbus import ModbusError, ModbusTcpClient  # noqa: E402
from solarmaa_dash.server import State, serve  # noqa: E402
from solarmaa_dash.sources import AnlagenQuelle as SunSpecSource, DemoSource, Snapshot  # noqa: E402
from solarmaa_dash.store import Store, ratios  # noqa: E402


def u16(v):
    return v & 0xFFFF


def build_inverter_map(ac_w=4321, w_sf=0, wh=12_345_678):
    """Registerbild: SunS, Modell 1, Modell 103, Modell 124, Ende."""
    regs = {}
    a = 40000
    for v in (0x5375, 0x6E53):
        regs[a] = v; a += 1
    regs[a], regs[a + 1] = 1, 66; a += 2 + 66
    regs[a], regs[a + 1] = 103, 50; start = a + 2
    block = [0] * 50
    block[12], block[13] = u16(ac_w), u16(w_sf)
    block[22], block[23], block[24] = wh >> 16, wh & 0xFFFF, 0
    block[29], block[30] = u16(4400), 0
    block[37] = 4
    for i, v in enumerate(block):
        regs[start + i] = v
    a = start + 50
    regs[a], regs[a + 1] = 124, 24; start = a + 2
    sto = [0] * 24
    sto[6], sto[20] = 655, u16(-1)  # 65.5 %
    for i, v in enumerate(sto):
        regs[start + i] = v
    a = start + 24
    regs[a], regs[a + 1] = 0xFFFF, 0
    return regs


def build_meter_map(w=-1500, imp=2_000_000, exp=5_000_000):
    regs = {40000: 0x5375, 40001: 0x6E53, 40002: 1, 40003: 65}
    a = 40004 + 65
    regs[a], regs[a + 1] = 203, 105; start = a + 2
    block = [0] * 105
    block[16], block[20] = u16(w), 0
    block[36], block[37] = exp >> 16, exp & 0xFFFF
    block[44], block[45] = imp >> 16, imp & 0xFFFF
    block[52] = 0
    for i, v in enumerate(block):
        regs[start + i] = v
    regs[start + 105], regs[start + 106] = 0xFFFF, 0
    return regs


class FakeModbus(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, units):
        self.units = units  # unit-id -> {addr: value}
        self.writes = []
        super().__init__(("127.0.0.1", 0), FakeHandler)


class FakeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            hdr = self.request.recv(7)
            if len(hdr) < 7:
                return
            tid, _p, length, unit = struct.unpack(">HHHB", hdr)
            pdu = self.request.recv(length - 1)
            if pdu[0] == 16:  # Write Multiple Registers: mitschneiden
                _fc, addr, count, _bc = struct.unpack(">BHHB", pdu[:6])
                werte = list(struct.unpack(f">{count}H", pdu[6:6 + 2 * count]))
                if getattr(self.server, "write_fail", False):
                    resp = struct.pack(">BB", 0x90, 4)
                    self.request.sendall(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
                    continue
                getattr(self.server, "writes", []).append((unit, addr, werte))
                for i, v in enumerate(werte):
                    self.server.units.setdefault(unit, {})[addr + i] = v
                resp = struct.pack(">BHH", 16, addr, count)
                self.request.sendall(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
                continue
            fc, addr, count = struct.unpack(">BHH", pdu)
            regs = self.server.units.get(unit, {})
            if any(addr + i not in regs for i in range(count)):
                resp = struct.pack(">BB", 0x83, 2)
            else:
                data = [regs[addr + i] for i in range(count)]
                resp = struct.pack(">BB", 3, 2 * count) + struct.pack(f">{count}H", *data)
            self.request.sendall(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)


class ModbusSunSpecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FakeModbus({1: build_inverter_map(), 240: build_meter_map()})
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_read_holding_and_exception(self):
        c = ModbusTcpClient("127.0.0.1", self.port, 1)
        self.assertEqual(c.read_holding(40000, 2), [0x5375, 0x6E53])
        with self.assertRaises(ModbusError):
            c.read_holding(1, 2)  # nicht vorhanden -> Exception-Antwort
        c.close()

    def test_discover_models(self):
        models = sunspec.discover(ModbusTcpClient("127.0.0.1", self.port, 1))
        self.assertEqual(sorted(models), [1, 103, 124])
        self.assertEqual(models[103].length, 50)

    def test_source_reads_everything(self):
        cfg = config._merge(config.DEFAULTS, {"quelle": {
            "typ": "sunspec",
            "wechselrichter": {"host": "127.0.0.1", "port": self.port, "unit": 1},
            "zaehler": {"host": "127.0.0.1", "port": self.port, "unit": 240, "netz_vorzeichen": 1},
            "speicher": {"host": "127.0.0.1", "port": self.port, "unit": 1},
        }})
        s = SunSpecSource(cfg).read()
        self.assertEqual(s.pv_w, 4321)
        self.assertEqual(s.grid_w, -1500)      # Einspeisung
        self.assertEqual(s.load_w, 2821)       # 4321 - 1500
        self.assertAlmostEqual(s.soc, 65.5)
        self.assertEqual(s.pv_wh_total, 12_345_678)
        self.assertEqual(s.import_wh_total, 2_000_000)

    def test_inverted_meter_sign(self):
        cfg = config._merge(config.DEFAULTS, {"quelle": {
            "typ": "sunspec",
            "wechselrichter": {"host": "127.0.0.1", "port": self.port, "unit": 1},
            "zaehler": {"host": "127.0.0.1", "port": self.port, "unit": 240, "netz_vorzeichen": -1},
        }})
        s = SunSpecSource(cfg).read()
        self.assertEqual(s.grid_w, 1500)
        self.assertEqual(s.import_wh_total, 5_000_000)

    def test_unreachable_device_raises(self):
        cfg = config._merge(config.DEFAULTS, {"quelle": {
            "typ": "sunspec", "wechselrichter": {"host": "127.0.0.1", "port": 1, "unit": 1}}})
        with self.assertRaises(Exception):
            SunSpecSource(cfg).read()


class SunSpecParsingTests(unittest.TestCase):
    def test_scale_and_not_implemented(self):
        self.assertIsNone(sunspec.int16(0x8000))
        self.assertEqual(sunspec.int16(0xFFFF), -1)
        self.assertEqual(sunspec.scale(1234, -1), 123.4)
        self.assertIsNone(sunspec.scale(None, 0))
        self.assertIsNone(sunspec.acc32(0, 0))


class StoreTests(unittest.TestCase):
    def test_energy_integration_and_ratios(self):
        st = Store(":memory:")
        t0 = time.mktime(time.strptime("2026-06-01 12:00:00", "%Y-%m-%d %H:%M:%S"))
        st._day = "2026-06-01"
        st._energy = st._load_day(st._day)
        # 1 Stunde lang: 3000 W PV, 1000 W Verbrauch, 2000 W Einspeisung
        for i in range(0, 3601, 5):
            st.add(Snapshot(t0 + i, 3000.0, -2000.0, 1000.0))
        st.flush()
        d = st.days("2026-06-01", "2026-06-01")[0]
        self.assertAlmostEqual(d["pv_wh"], 3000, delta=1)
        self.assertAlmostEqual(d["export_wh"], 2000, delta=1)
        self.assertAlmostEqual(d["eigenverbrauch_pct"], 33.3, delta=0.2)
        self.assertEqual(d["autarkie_pct"], 100.0)
        series = st.day_series("2026-06-01")
        self.assertEqual(len(series), 13)  # 12:00 … 13:00 in 5-Minuten-Schritten
        self.assertAlmostEqual(series[0][1], 3000.0)
        self.assertEqual(st.months(2026)[0]["month"], "2026-06")

    def test_gap_not_integrated(self):
        st = Store(":memory:")
        t0 = time.mktime(time.strptime("2026-06-02 10:00:00", "%Y-%m-%d %H:%M:%S"))
        st.add(Snapshot(t0, 1000.0, 0.0, 1000.0))
        st.add(Snapshot(t0 + 3600, 1000.0, 0.0, 1000.0))  # 1 h Lücke
        self.assertEqual(st.today()["pv_wh"] if st._day == "2026-06-02" else st._energy["pv_wh"], 0.0)

    def test_ratios_none_when_tiny(self):
        self.assertIsNone(ratios({"pv_wh": 10, "load_wh": 10})["eigenverbrauch_pct"])


class DemoTests(unittest.TestCase):
    def test_energy_balance(self):
        d = DemoSource(kwp=10)
        t = time.mktime(time.strptime("2026-06-21 13:00:00", "%Y-%m-%d %H:%M:%S"))
        for i in range(20):
            s = d.read(t + i * 5)
            self.assertAlmostEqual(s.load_w, s.pv_w + s.grid_w + s.battery_w, places=6)
            self.assertTrue(0 <= s.soc <= 100)
        night = d.read(time.mktime(time.strptime("2026-06-22 02:00:00", "%Y-%m-%d %H:%M:%S")))
        self.assertEqual(night.pv_w, 0.0)


class ServerTests(unittest.TestCase):
    def test_api_endpoints(self):
        cfg = config._merge(config.DEFAULTS, {"anlage": {"name": "Test"}})
        store = Store(":memory:")
        state = State(cfg, store, "demo")
        snap = DemoSource().read()
        store.add(snap)
        state.publish(snap)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        httpd = serve(state, ROOT / "app", "127.0.0.1", port)
        try:
            base = f"http://127.0.0.1:{port}"
            now = json.load(urllib.request.urlopen(base + "/api/now"))
            self.assertEqual(now["status"], "ok")
            self.assertTrue(now["demo"])
            info = json.load(urllib.request.urlopen(base + "/api/info"))
            self.assertEqual(info["anlage"]["name"], "Test")
            hist = json.load(urllib.request.urlopen(base + "/api/history?range=week"))
            self.assertEqual(hist["range"], "week")
            html = urllib.request.urlopen(base + "/").read()
            self.assertIn(b"solarmaa", html)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/history?range=decade")
            self.assertEqual(cm.exception.code, 400)
        finally:
            httpd.shutdown()
            httpd.server_close()


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self):
        cfg = config.load(ROOT / "pi" / "config.example.toml")
        self.assertIn(cfg["quelle"]["typ"], ("demo", "sunspec"))

    def test_sunspec_requires_host(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write('[quelle]\ntyp = "sunspec"\n')
        with self.assertRaises(ValueError):
            config.load(fh.name)


if __name__ == "__main__":
    unittest.main()
