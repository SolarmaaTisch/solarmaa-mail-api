"""Tests für Geräte-Adapter, Haus/ZEV, Login, Rechnungen und Erkennung."""
import json
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))
sys.path.insert(0, str(ROOT / "tests"))

from solarmaa_dash import config, erkennung  # noqa: E402
from solarmaa_dash.auth import Auth, check_pw, hash_pw  # noqa: E402
from solarmaa_dash.geraete import GeraetFehler  # noqa: E402
from solarmaa_dash.geraete.fronius import Fronius  # noqa: E402
from solarmaa_dash.geraete.modbus_profil import ModbusProfilGeraet, dekodiere, lade_profil  # noqa: E402
from solarmaa_dash.geraete.shelly import Shelly  # noqa: E402
from solarmaa_dash.geraete.smartme import SmartMeCloudZaehler, _KONTEN  # noqa: E402
from solarmaa_dash.haus import Haus  # noqa: E402
from solarmaa_dash.rechnungen import Rechnungen, _betrag  # noqa: E402
from solarmaa_dash.server import State, serve  # noqa: E402
from solarmaa_dash.sources import AnlagenQuelle, DemoSource, Snapshot, SocSchaetzer  # noqa: E402
from solarmaa_dash.store import Store  # noqa: E402
from test_dash import FakeModbus, u16  # noqa: E402


class FakeHttp(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, routes, need_key=None):
        self.routes, self.need_key = routes, need_key
        self.posts, self.gets = [], 0
        super().__init__(("127.0.0.1", 0), FakeHttpHandler)


class FakeHttpHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.server.posts.append((self.path, json.loads(self.rfile.read(n) or b"null")))
        body = self.server.routes.get("POST " + self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self.server.gets += 1
        if self.server.need_key and self.headers.get("Authorization") != self.server.need_key:
            self.send_response(401); self.end_headers(); return
        body = self.server.routes.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_http(routes, need_key=None):
    srv = FakeHttp(routes, need_key)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"127.0.0.1:{srv.server_address[1]}"


class ShellyFroniusSmartMeTests(unittest.TestCase):
    def test_shelly_gen2_pro3em(self):
        srv, host = start_http({
            "/shelly": {"gen": 2, "model": "SPEM-003CEBEU"},
            "/rpc/Shelly.GetStatus": {"em:0": {"total_act_power": -1234.5},
                                      "emdata:0": {"total_act": 1000.0, "total_act_ret": 2000.0}}})
        try:
            m = Shelly({"host": host}).lesen()
            self.assertEqual(m.w, -1234.5)
            self.assertEqual((m.import_wh, m.export_wh), (1000.0, 2000.0))
            m2 = Shelly({"host": host, "vorzeichen": -1}).lesen()
            self.assertEqual(m2.w, 1234.5)
            self.assertEqual(m2.import_wh, 2000.0)
        finally:
            srv.shutdown(); srv.server_close()

    def test_shelly_gen2_switch_and_gen1(self):
        srv, host = start_http({
            "/shelly": {"gen": 3},
            "/rpc/Shelly.GetStatus": {"sys": {}, "switch:0": {"apower": 812.0, "aenergy": {"total": 5000.0}}}})
        srv1, host1 = start_http({
            "/shelly": {"type": "SHEM-3"},
            "/status": {"emeters": [{"power": 100, "total": 10, "total_returned": 1},
                                    {"power": 200, "total": 20, "total_returned": 2},
                                    {"power": -50, "total": 30, "total_returned": 3}]}})
        try:
            self.assertEqual(Shelly({"host": host}).lesen().w, 812.0)
            m = Shelly({"host": host1}).lesen()
            self.assertEqual((m.w, m.import_wh, m.export_wh), (250.0, 60.0, 6.0))
        finally:
            for s in (srv, srv1):
                s.shutdown(); s.server_close()

    def test_fronius(self):
        srv, host = start_http({"/solar_api/v1/GetPowerFlowRealtimeData.fcgi": {"Body": {"Data": {
            "Site": {"P_PV": 5000, "P_Grid": -1500, "P_Akku": -1000, "P_Load": -2500, "E_Total": 123456},
            "Inverters": {"1": {"SOC": 55.5}}}}}})
        try:
            m = Fronius({"host": host}).lesen()
            self.assertEqual((m.pv_w, m.grid_w, m.battery_w, m.soc), (5000.0, -1500.0, -1000.0, 55.5))
        finally:
            srv.shutdown(); srv.server_close()

    def test_smartme_cloud_with_api_key(self):
        _KONTEN.clear()
        devices = [{"Id": "abc-1", "Name": "Wohnung 1", "Serial": 7100200, "ActivePower": 1.25,
                    "ActivePowerUnit": "kW", "CounterReading": 1500.5, "CounterReadingImport": 1500.5,
                    "CounterReadingExport": 0.0, "CounterReadingUnit": "kWh"},
                   {"id": "abc-2", "serial": 7100201, "activePower": 0.3}]
        srv, host = start_http({"/Devices": devices}, need_key="ApiKey geheim")
        try:
            sm = {"basis_url": f"http://{host}", "api_key": "geheim"}
            m = SmartMeCloudZaehler({"_smartme": sm, "seriennummer": "7100200"}).lesen()
            self.assertEqual(m.w, 1250.0)
            self.assertEqual(m.import_wh, 1500500.0)
            m2 = SmartMeCloudZaehler({"_smartme": sm, "smartme_id": "ABC-2"}).lesen()
            self.assertEqual(m2.w, 300.0)
            with self.assertRaises(GeraetFehler):
                SmartMeCloudZaehler({"_smartme": sm, "seriennummer": "999"}).lesen()
            _KONTEN.clear()
            with self.assertRaises(GeraetFehler):
                SmartMeCloudZaehler({"_smartme": {**sm, "api_key": "falsch"}, "seriennummer": "7100200"}).lesen()
        finally:
            _KONTEN.clear()
            srv.shutdown(); srv.server_close()


class ProfilTests(unittest.TestCase):
    def test_alle_profile_gueltig(self):
        for p in (ROOT / "pi" / "solarmaa_dash" / "profile").glob("*.toml"):
            import tomllib
            if "steuerung" in tomllib.loads(p.read_text()) and "werte" not in tomllib.loads(p.read_text()):
                from solarmaa_dash.abregelung import lade_steuerprofil
                self.assertIn("limit_register", lade_steuerprofil(p.stem)["steuerung"])
                continue
            if p.stem == "smartme_lokal":
                with self.assertRaises(ValueError):
                    lade_profil(p.stem)
            else:
                prof = lade_profil(p.stem)
                self.assertIn("erkennung", prof, p.stem)

    def test_dekodiere(self):
        self.assertEqual(dekodiere([0xFFFF, 0xFC18], "i32", "gross"), -1000)
        self.assertEqual(dekodiere([0xFC18, 0xFFFF], "i32", "klein"), -1000)
        self.assertIsNone(dekodiere([0x8000], "i16", "gross"))
        self.assertAlmostEqual(dekodiere([0x4148, 0x0000], "f32", "gross"), 12.5)

    def test_sigenergy_profil_gegen_fake(self):
        def s32(v):
            v &= 0xFFFFFFFF
            return [v >> 16, v & 0xFFFF]
        regs = {}
        for addr, val in ((30035, 6000), (30005, -2000), (30037, 1500)):
            regs[addr], regs[addr + 1] = s32(val)
        regs[30014] = 734
        srv = FakeModbus({247: regs})
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            dev = ModbusProfilGeraet({"profil": "sigenergy", "host": "127.0.0.1",
                                      "port": srv.server_address[1], "unit": 247})
            m = dev.lesen()
            self.assertEqual((m.pv_w, m.grid_w, m.battery_w), (6000.0, -2000.0, -1500.0))  # Laden = negativ
            self.assertAlmostEqual(m.soc, 73.4)
        finally:
            srv.shutdown(); srv.server_close()


class AnlagenQuelleTests(unittest.TestCase):
    def test_kombination_shelly_pv_und_netz(self):
        srv, host = start_http({"/shelly": {"gen": 2},
                                "/rpc/Shelly.GetStatus": {"em:0": {"total_act_power": -3000.0}, "emdata:0": {}}})
        srv2, host2 = start_http({"/shelly": {"gen": 2},
                                  "/rpc/Shelly.GetStatus": {"em:0": {"total_act_power": -1000.0}, "emdata:0": {}}})
        try:
            cfg = config._merge(config.DEFAULTS, {"geraet": [
                {"name": "PV-Abgang", "rolle": "pv", "typ": "shelly", "host": host},
                {"name": "Netz", "rolle": "netz", "typ": "shelly", "host": host2}]})
            s = AnlagenQuelle(cfg).read()
            self.assertEqual(s.pv_w, 3000.0)       # Zangenrichtung egal
            self.assertEqual(s.grid_w, -1000.0)    # Einspeisung
            self.assertEqual(s.load_w, 2000.0)
        finally:
            for x in (srv, srv2):
                x.shutdown(); x.server_close()

    def test_speicher_fehler_ist_optional(self):
        srv, host = start_http({"/solar_api/v1/GetPowerFlowRealtimeData.fcgi": {"Body": {"Data": {
            "Site": {"P_PV": 4000, "P_Grid": 500, "P_Akku": 300}}}}})
        try:
            cfg = config._merge(config.DEFAULTS, {"geraet": [
                {"name": "Fronius", "rolle": "anlage", "typ": "fronius", "host": host},
                {"name": "Akku", "rolle": "speicher", "typ": "shelly", "host": "127.0.0.1:1"}]})
            q = AnlagenQuelle(cfg)
            s = q.read()
            self.assertEqual(s.load_w, 4800.0)
            self.assertNotEqual(q.status["Akku"], "ok")
        finally:
            srv.shutdown(); srv.server_close()


class EvoliumTests(unittest.TestCase):
    def test_soc_energiebilanz_und_kalibrierung(self):
        s = SocSchaetzer(10, wirkungsgrad=0.9)
        t = 1000.0
        s.update(t, 0.0, 0.0)
        for i in range(1, 61):                         # 1 h mit 1 kW laden
            soc = s.update(t + i * 60, -1000.0, 0.0)
        self.assertAlmostEqual(soc, 59.0, places=1)    # 50 % + 900 Wh
        self.assertFalse(s.kalibriert)
        t2 = t + 3600
        for i in range(1, 7):                          # 6 min: Überschuss, Speicher lädt nicht -> voll
            soc = s.update(t2 + i * 60, 0.0, -800.0)
        self.assertEqual(soc, 100.0)
        self.assertTrue(s.kalibriert)
        t3 = t2 + 600
        for i in range(0, 7):                          # 6 min: Netzbezug, Speicher entlädt nicht -> leer
            soc = s.update(t3 + i * 60, 0.0, 900.0)
        self.assertEqual(soc, 0.0)

    def test_evolium_ueber_shelly(self):
        # Shelly am Speicher-Abgang: Laden = Bezug (+), darum vorzeichen = -1
        srv, host = start_http({"/shelly": {"gen": 2},
                                "/rpc/Shelly.GetStatus": {"switch:0": {"apower": 1500.0}}})
        srv2, host2 = start_http({"/shelly": {"gen": 2},
                                  "/rpc/Shelly.GetStatus": {"em:0": {"total_act_power": -500.0}, "emdata:0": {}}})
        srv3, host3 = start_http({"/shelly": {"gen": 2},
                                  "/rpc/Shelly.GetStatus": {"em:0": {"total_act_power": -3000.0}, "emdata:0": {}}})
        try:
            cfg = config._merge(config.DEFAULTS, {"daten": {"pfad": ":memory:"}, "geraet": [
                {"name": "PV", "rolle": "pv", "typ": "shelly", "host": host3},
                {"name": "Netz", "rolle": "netz", "typ": "shelly", "host": host2},
                {"name": "Evolium", "rolle": "speicher", "typ": "shelly", "host": host, "vorzeichen": -1,
                 "soc_schaetzen": True, "kapazitaet_kwh": 10}]})
            s = AnlagenQuelle(cfg).read()
            self.assertEqual(s.battery_w, -1500.0)       # lädt
            self.assertEqual(s.load_w, 1000.0)           # 3000 PV − 500 Einspeisung − 1500 Laden
            self.assertTrue(s.soc_geschaetzt)
            self.assertAlmostEqual(s.soc, 50.0)
        finally:
            for x in (srv, srv2, srv3):
                x.shutdown(); x.server_close()


class SteckerWallboxTests(unittest.TestCase):
    def test_mystrom(self):
        srv, host = start_http({"/report": {"power": 1234.5, "relay": True, "Ws": 1200.0,
                                            "temperature": 24.1, "energy_since_boot": 7200000.0}})
        try:
            from solarmaa_dash.geraete.mystrom import MyStrom
            m = MyStrom({"host": host}).lesen()
            self.assertEqual((m.w, m.import_wh, m.extra["relais"]), (1234.5, 2000.0, True))
        finally:
            srv.shutdown(); srv.server_close()

    def test_whatwatt(self):
        rep = {"report": {"instantaneous_power": {"active": {"positive": {"total": 0.0}, "negative": {"total": 2.35}}},
                          "energy": {"active": {"positive": {"total": 12345.6}, "negative": {"total": 7890.1}}}}}
        srv, host = start_http({"/api/v1/report": rep, "/api/v1/system": {"device": {"model": "whatwatt Go"}}})
        try:
            from solarmaa_dash.geraete.whatwatt import WhatWatt
            m = WhatWatt({"host": host}).lesen()                      # Standard kW / kWh
            self.assertAlmostEqual(m.w, -2350.0)                        # Einspeisung
            self.assertAlmostEqual(m.import_wh, 12345600.0)
            m2 = WhatWatt({"host": host, "leistung_einheit": "W", "energie_einheit": "Wh"}).lesen()
            self.assertAlmostEqual(m2.w, -2.35)
        finally:
            srv.shutdown(); srv.server_close()

    def test_goe(self):
        nrg = [230, 231, 229, 0, 16, 16, 16, 3680, 3690, 3670, 0, 11040, 99, 99, 99, 0]
        srv, host = start_http({"/api/status?filter=nrg,car,wh,eto,amp":
                                {"nrg": nrg, "car": 2, "wh": 5300.5, "eto": 1234000, "amp": 16}})
        try:
            from solarmaa_dash.geraete.goe import GoE
            m = GoE({"host": host}).lesen()
            self.assertEqual((m.w, m.import_wh), (11040.0, 1234000.0))
            self.assertEqual((m.extra["status"], m.extra["laedt"], m.extra["sitzung_wh"]), ("lädt", True, 5300.5))
        finally:
            srv.shutdown(); srv.server_close()

    def test_easee_cloud(self):
        from solarmaa_dash.geraete import easee
        easee._KONTEN.clear()
        srv, host = start_http({
            "POST /api/accounts/login": {"accessToken": "tok", "refreshToken": "ref", "expiresIn": 3600},
            "/api/chargers/EH123456/state": {"totalPower": 7.2, "chargerOpMode": 3, "sessionEnergy": 12.5,
                                             "lifetimeEnergy": 3456.7, "isOnline": True}})
        try:
            cfg = {"_easee": {"benutzer": "a@b.ch", "passwort": "x", "basis_url": f"http://{host}"},
                   "charger_id": "EH123456"}
            dev = easee.Easee(cfg)
            m = dev.lesen()
            self.assertEqual((m.w, m.extra["status"], m.extra["sitzung_wh"]), (7200.0, "lädt", 12500.0))
            self.assertAlmostEqual(m.import_wh, 3456700.0)
            dev.lesen()                                   # aus dem Zwischenspeicher
            self.assertEqual(srv.gets, 1)
            self.assertEqual(srv.posts[0][1]["userName"], "a@b.ch")
        finally:
            easee._KONTEN.clear()
            srv.shutdown(); srv.server_close()

    def test_wallbox_als_verbraucher(self):
        nrg = [0] * 11 + [3700] + [0] * 4
        srv, host = start_http({"/api/status?filter=nrg,car,wh,eto,amp": {"nrg": nrg, "car": 2, "eto": 1}})
        srv2, host2 = start_http({"/shelly": {"gen": 2},
                                  "/rpc/Shelly.GetStatus": {"em:0": {"total_act_power": 4200.0}, "emdata:0": {}}})
        try:
            cfg = config._merge(config.DEFAULTS, {"geraet": [
                {"name": "Netz", "rolle": "netz", "typ": "shelly", "host": host2},
                {"name": "Wallbox", "rolle": "verbraucher", "typ": "goe", "host": host},
                {"name": "Kaputt", "rolle": "verbraucher", "typ": "mystrom", "host": "127.0.0.1:1"}]})
            s = AnlagenQuelle(cfg).read()
            self.assertEqual(s.load_w, 4200.0)          # Wallbox ist im Verbrauch enthalten
            self.assertEqual(s.ev_w, 3700.0)
            self.assertEqual([v["name"] for v in s.verbraucher], ["Wallbox", "Kaputt"])
            self.assertIsNone(s.verbraucher[1]["w"])    # ausgefallener Stecker bricht nichts ab
            st = Store(":memory:")
            st.add(s)
            st.add(Snapshot(s.ts + 60, 0.0, 4200.0, 4200.0, verbraucher=s.verbraucher))
            self.assertAlmostEqual(st.today()["ev_wh"], 3700 / 60, places=1)
        finally:
            for x in (srv, srv2):
                x.shutdown(); x.server_close()


def haus_cfg():
    return config._merge(config.DEFAULTS, {"haus": {
        "aktiv": True, "demo": True, "name": "Testhaus", "geschosse": ["UG", "EG"],
        "einheit": [
            {"id": "a", "name": "Wohnung A", "geschoss": "EG", "zaehler": [{"name": "Z"}]},
            {"id": "b", "name": "Wohnung B", "geschoss": "EG", "smartme_ordner": "Whg B", "zaehler": [{"name": "Z"}]},
            {"id": "allg", "name": "Allgemein", "geschoss": "UG", "typ": "allgemein", "zaehler": [{"name": "Z"}]}]}})


class HausRechnungenAuthTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.haus = Haus(haus_cfg(), self.store)
        self.auth = Auth(self.store)
        self.rg = Rechnungen(self.store, self.haus)

    def test_solar_verteilung(self):
        self.haus.tick(Snapshot(time.time(), 1000.0, -200.0, 800.0))
        w = {e["id"]: self.haus.einheit_w(e) for e in self.haus.einheiten}
        summe = sum(w.values())
        verteilt = sum(self.haus.solar_verteilung.values())
        self.assertAlmostEqual(verteilt, min(800.0, summe), places=6)
        self.haus._last_ts -= 30
        self.haus.tick(Snapshot(time.time(), 1000.0, -200.0, 800.0))
        self.assertGreater(self.haus.heute("a")["verbrauch_kwh"] + 1e-9, 0)

    def test_passwort_und_rollen(self):
        h = hash_pw("sehr-geheim-123")
        self.assertTrue(check_pw("sehr-geheim-123", h))
        self.assertFalse(check_pw("falsch", h))
        with self.assertRaises(ValueError):
            self.auth.anlegen("m", "kurz", "mieter", "a")
        with self.assertRaises(ValueError):
            self.auth.anlegen("m", "lang-genug-123", "mieter")
        self.auth.anlegen("m", "lang-genug-123", "mieter", "a")
        self.auth.anlegen("v", "lang-genug-123", "verwaltung")
        self.assertIsNone(self.auth.login("m", "falsch-123456", "1.2.3.4"))
        tok = self.auth.login("m", "lang-genug-123", "1.2.3.4")
        self.assertEqual(self.auth.benutzer(tok)["einheit"], "a")
        self.assertEqual({b["name"] for b in self.auth.berechtigte("a")}, {"m", "v"})
        self.assertEqual({b["name"] for b in self.auth.berechtigte("b")}, {"v"})
        self.auth.logout(tok)
        self.assertIsNone(self.auth.benutzer(tok))

    def test_csv_import(self):
        csv = ("﻿Rechnungsnummer;Wohnung;Empfänger;Periode von;Periode bis;Total CHF;Status;Fällig am\n"
               "R-1;Wohnung A;Muster;01.01.2026;31.03.2026;CHF 1'234.50;offen;30.04.2026\n"
               "R-2;Whg B;Beispiel;01.01.2026;31.03.2026;99,90;bezahlt;30.04.2099\n"
               "R-3;Unbekannt;X;;;10;;\n")
        r = self.rg.importieren(csv)
        self.assertEqual((r["neu"], r["aktualisiert"]), (2, 0))
        self.assertEqual(len(r["nicht_zugeordnet"]), 1)
        a = self.rg.liste("a")[0]
        self.assertEqual((a["betrag"], a["status"], a["von"]), (1234.5, "offen", "2026-01-01"))
        self.assertTrue(a["ueberfaellig"])
        self.assertEqual(self.rg.liste("b")[0]["betrag"], 99.9)
        self.assertTrue(self.rg.status_setzen("R-1", "bezahlt", "test"))
        self.assertEqual(self.rg.offen_summe(), {})
        with self.assertRaises(ValueError):
            self.rg.importieren("A;B\n1;2\n")

    def test_betrag(self):
        self.assertEqual(_betrag("1'234.50"), 1234.5)
        self.assertEqual(_betrag("1.234,50"), 1234.5)
        self.assertEqual(_betrag("CHF 12.00"), 12.0)


class HausServerTests(unittest.TestCase):
    def test_rollen_ueber_http(self):
        cfg = haus_cfg()
        store = Store(":memory:")
        haus = Haus(cfg, store)
        auth = Auth(store)
        rg = Rechnungen(store, haus)
        rg.importieren("Nr;Einheit;Betrag\nR-9;b;50\n")
        auth.anlegen("mieter", "lang-genug-123", "mieter", "a")
        auth.anlegen("admin", "lang-genug-123", "admin")
        state = State(cfg, store, "demo", None, haus, auth, rg)
        state.publish(DemoSource().read())
        haus.tick(state.latest)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        httpd = serve(state, ROOT / "app", "127.0.0.1", port)
        base = f"http://127.0.0.1:{port}"

        def call(path, body=None, cookie=None, header=True):
            req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                         method="GET" if body is None else "POST")
            if header and body is not None:
                req.add_header("X-Solarmaa", "1")
            if cookie:
                req.add_header("Cookie", cookie)
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read()), r.headers.get("Set-Cookie")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read()), None

        try:
            self.assertEqual(call("/api/haus")[0], 401)
            self.assertEqual(call("/api/login", {"benutzer": "mieter", "passwort": "lang-genug-123"}, header=False)[0], 403)
            st, _, ck = call("/api/login", {"benutzer": "mieter", "passwort": "lang-genug-123"})
            self.assertEqual(st, 200)
            ck = ck.split(";")[0]
            st, h, _ = call("/api/haus", cookie=ck)
            zug = {e["id"]: e["zugriff"] for e in h["einheiten"]}
            self.assertEqual(zug, {"a": True, "b": False, "allg": False})
            self.assertNotIn("w", next(e for e in h["einheiten"] if e["id"] == "b"))
            self.assertEqual(call("/api/einheit?id=b", cookie=ck)[0], 403)
            st, d, _ = call("/api/einheit?id=a", cookie=ck)
            self.assertEqual(st, 200)
            self.assertNotIn("berechtigte", d)
            self.assertEqual(call("/api/rechnungen", cookie=ck)[0], 403)
            self.assertEqual(call("/api/rechnungen/status", {"nr": "R-9", "status": "bezahlt"}, cookie=ck)[0], 403)
            _, _, ca = call("/api/login", {"benutzer": "admin", "passwort": "lang-genug-123"})
            ca = ca.split(";")[0]
            st, d, _ = call("/api/einheit?id=b", cookie=ca)
            self.assertEqual(d["rechnungen"][0]["nr"], "R-9")
            self.assertIn("berechtigte", d)
            self.assertEqual(call("/api/rechnungen/status", {"nr": "R-9", "status": "bezahlt"}, cookie=ca)[0], 200)
        finally:
            httpd.shutdown(); httpd.server_close()


class ErkennungTests(unittest.TestCase):
    def test_toml_vorschlag(self):
        funde = [{"host": "10.0.0.5", "typ": "shelly", "rolle": "netz", "modell": "Shelly Pro 3EM", "hinweis": "Rolle prüfen"},
                 {"host": "10.0.0.6", "typ": "modbus_profil", "profil": "sigenergy", "unit": 247, "rolle": "anlage",
                  "modell": "Sigenergy", "hinweis": ""},
                 {"host": "smart-me Cloud", "typ": "smartme_cloud", "rolle": "einheit", "modell": "Whg 1", "seriennummer": "71"}]
        t = erkennung.als_toml(funde)
        import tomllib
        d = tomllib.loads(t)
        self.assertEqual([g["typ"] for g in d["geraet"]], ["shelly", "modbus_profil"])
        self.assertEqual(d["geraet"][1]["unit"], 247)
        self.assertIn('seriennummer = "71"', t)


if __name__ == "__main__":
    unittest.main()
