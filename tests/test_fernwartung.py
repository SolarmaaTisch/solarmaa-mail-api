"""Tests für die Fernwartung über das Portal (Stufe 3). Portal und Update-Weg sind simuliert."""
import hashlib
import json
import logging
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pi"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from solarmaa_dash import config, konfig_web, wartung  # noqa: E402
from solarmaa_dash.fernwartung import DATEI, Fernwartung, ruecksetzen_falls_probe  # noqa: E402
from solarmaa_dash.konfig_web import (MASKE, KonfigWeb, demaskieren, maskieren, sicher_maskiert,  # noqa: E402
                                      uebernahme_pruefen)
from solarmaa_dash.portal import Portal  # noqa: E402
from solarmaa_dash.server import State  # noqa: E402
from solarmaa_dash.sources import DemoSource  # noqa: E402
from solarmaa_dash.store import Store  # noqa: E402
from test_erweiterung import start_http  # noqa: E402

logging.getLogger("solarmaa_dash").addHandler(logging.NullHandler())      # Testausgabe ruhig halten

TOKEN = "t" * 48
API_KEY = "smartme-schluessel-4711"
PASSWORT = "easee-passwort-0815"
ID = "00000000-0000-4000-8000-0000000000{:02d}".format
ERGEBNIS = "/rest/v1/rpc/pi_ergebnis"
KONFIG = "/rest/v1/rpc/pi_konfig"


def basis(text: str) -> str:
    return hashlib.sha256(maskieren(text).encode()).hexdigest()


class Umgebung:
    """Ein Pi mit Konfigurationsdatei im Temp-Ordner und einem simulierten Portal."""

    def __init__(self, test: unittest.TestCase, fernwartung="true", routen=None):
        self.d = Path(tempfile.mkdtemp())
        self.srv, host = start_http({"POST " + ERGEBNIS: True, "POST " + KONFIG: True} if routen is None else routen)
        test.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close()))
        self.pfad = self.d / "config.toml"
        self.text = (f'[anlage]\nname = "Alt"\n\n[smartme]\nbasis_url = "https://api.smart-me.com"\n'
                     f'api_key = "{API_KEY}"\n\n[easee]\nbenutzer = "tin"\npasswort = "{PASSWORT}"\n\n'
                     f'[portal]\nurl = "http://{host}"\nschluessel = "k"\ntoken = "{TOKEN}"\n'
                     f'fernwartung = {fernwartung}\n\n[daten]\npfad = "{self.d}/data.db"\n')
        self.pfad.write_text(self.text, encoding="utf-8")
        self.neustarts = 0
        self.neu()

    def neu(self) -> Fernwartung:
        """Baut alles neu auf wie nach einem Neustart des Dienstes (gleicher Ordner)."""
        cfg = config.load(self.pfad)
        self.portal = Portal(cfg)
        konfig = KonfigWeb(str(self.pfad), self.d)
        konfig.neustart = self._neustart
        self.state = types.SimpleNamespace(konfig=konfig, wartung=wartung.Wartung(cfg, self.d), abregelung=None)
        self.fw = Fernwartung(self.portal, self.state, self.d)
        return self.fw

    def _neustart(self, *a):
        self.neustarts += 1

    def gesendet(self, pfad: str) -> list:
        return [b for p, b in self.srv.posts if p == pfad]

    def quittungen(self) -> list:
        return [(b["p_befehl"], b["p_ok"], b["p_text"]) for b in self.gesendet(ERGEBNIS)]

    def konfig_befehl(self, nr: int, text: str, basis_: str | None = None) -> dict:
        return {"id": ID(nr), "art": "konfig",
                "nutzlast": {"text": text, "basis": basis(self.pfad.read_text()) if basis_ is None else basis_}}

    def lauf(self, befehle) -> None:
        self.fw.verarbeiten(befehle)
        self.fw.nachlauf()


class PortalVerbindungTests(unittest.TestCase):
    def _state(self, cfg):
        store = Store(":memory:")
        state = State(cfg, store, "demo")
        snap = DemoSource().read()
        store.add(snap); state.publish(snap)
        return state

    def test_melden_liefert_befehle(self):
        befehl = {"id": ID(1), "art": "update", "nutzlast": None}
        srv, host = start_http({"POST /rest/v1/rpc/pi_melden": {"ok": True, "befehle": [befehl]}})
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        cfg = config._merge(config.DEFAULTS, {"portal": {"url": f"http://{host}", "schluessel": "k", "token": TOKEN,
                                                         "fernwartung": True}})
        p, state = Portal(cfg), self._state(cfg)
        self.assertEqual(p.melden(state)["befehle"], [befehl])
        self.assertIs(srv.posts[0][1]["p_daten"]["fernwartung"], True)       # Schalter wird gemeldet
        self.assertIs(p.status()["fernwartung"], True)
        srv.routes["POST /rest/v1/rpc/pi_melden"] = [1, 2]                   # alte Datenbank / Unsinn
        self.assertEqual(p.melden(state), {})
        srv.routes.clear()
        self.assertIsNone(p.melden(state))
        self.assertEqual(p.rpc("pi_konfig", {"p_token": TOKEN, "p_text": ""})[0], 404)
        self.assertNotIn(TOKEN, p.fehler)

    def test_standard_ist_aus_und_nur_bool(self):
        cfg = config._merge(config.DEFAULTS, {"portal": {"url": "http://127.0.0.1:9", "schluessel": "k", "token": TOKEN}})
        p = Portal(cfg)
        self.assertIs(p.fernwartung, False)
        self.assertIs(p.nutzlast(self._state(cfg))["fernwartung"], False)
        with self.assertRaisesRegex(ValueError, "fernwartung muss true oder false"):
            Portal(config._merge(cfg, {"portal": {"fernwartung": "false"}}))
        self.assertEqual(p.rpc("pi_melden", {})[0], 0)                       # nicht erreichbar: keine Ausnahme

    def test_antwort_ohne_befehle_loest_nichts_aus(self):
        u = Umgebung(self)
        for antwort in (None, {}, {"ok": True}, {"befehle": None}, {"befehle": "update"}, {"befehle": [None, 7, {}]},
                        {"befehle": [{"id": "keine-uuid", "art": "update"}, {"id": ID(1)}]}):
            u.fw.verarbeiten((antwort or {}).get("befehle"))
        self.assertEqual(u.fw.z["gesehen"], [])
        self.assertIsNone(u.fw.z["wartend"])
        self.assertEqual(u.neustarts, 0)


class MaskierenTests(unittest.TestCase):
    def test_thema_und_lebenszeichen_sind_geheim(self):
        alt = ('[benachrichtigung]\nlebenszeichen_url = "https://hc-ping.com/abc-123"\n'
               '[benachrichtigung.push]\nthema = "solarmaa-kunde-7f3k"\n')
        m = maskieren(alt)
        self.assertNotIn("abc-123", m)
        self.assertNotIn("7f3k", m)
        self.assertEqual(m.count(MASKE), 2)
        self.assertEqual(demaskieren(m, alt), alt)
        self.assertEqual(sicher_maskiert(alt), m)

    def test_sicher_maskiert_verweigert_unerkannte_schreibweisen(self):
        for text in ("[easee]\npasswort = 'einfach-geheim'\n",
                     '[easee]\npasswort = """mehr\nzeilig"""\n',
                     '[benachrichtigung]\npush = { thema = "inline-geheim" }\n',
                     'easee.passwort = "punkt-geheim"\n',
                     '[smartme]\napi_key = "doppelt-geheim-1"\nbenutzer = "doppelt-geheim-1"\n',
                     "das ist [kein toml"):
            with self.assertRaises(ValueError, msg=text):
                sicher_maskiert(text)
        self.assertIn(MASKE, sicher_maskiert('[easee]\npasswort = "ok-geheim-123"\n'))
        self.assertEqual(sicher_maskiert('[easee]\npasswort = ""\n'), '[easee]\npasswort = ""\n')


class KonfigFernTests(unittest.TestCase):
    """Befehl «konfig»: was angenommen und was abgelehnt wird."""

    def test_gueltige_aenderung_wird_gespeichert_und_nach_neustart_quittiert(self):
        u = Umgebung(self)
        neu = maskieren(u.text).replace('"Alt"', '"Neu"')
        self.assertNotIn(API_KEY, neu)
        u.lauf([u.konfig_befehl(1, neu)])
        gespeichert = u.pfad.read_text()
        self.assertIn('name = "Neu"', gespeichert)
        self.assertIn(f'api_key = "{API_KEY}"', gespeichert)                # Geheimnisse erhalten
        self.assertIn(f'passwort = "{PASSWORT}"', gespeichert)
        sicherungen = list((u.d / "config-sicherung").glob("config-*-portal.toml"))
        self.assertEqual(len(sicherungen), 1)
        self.assertEqual(sicherungen[0].read_text(), u.text)
        self.assertEqual(u.neustarts, 1)
        self.assertEqual(u.quittungen(), [])                                 # noch nicht quittiert
        zustand = json.loads((u.d / DATEI).read_text())
        self.assertTrue(zustand["wartend"]["gespeichert"])
        self.assertNotIn(API_KEY, json.dumps(zustand))
        self.assertEqual(oct((u.d / DATEI).stat().st_mode & 0o777), "0o600")
        u.fw.nachlauf()                                                      # selber Prozess: weiter warten
        self.assertEqual(u.quittungen(), [])
        u.neu().nachlauf()                                                   # nach dem Neustart
        (bid, ok, text), = u.quittungen()
        self.assertEqual((bid, ok), (ID(1), True))
        self.assertIn("läuft mit neuer Konfiguration", text)
        self.assertIsNone(u.fw.z["wartend"])
        self.assertEqual(u.fw.z["quittungen"], [])

    def test_aenderung_in_abregelung_wird_angenommen(self):
        u = Umgebung(self)
        u.lauf([u.konfig_befehl(1, maskieren(u.text) + '\n[abregelung]\nschwelle = -5.0\n'
                                                       '\n[benachrichtigung.push]\nthema = "neues-thema-123"\n')])
        self.assertIn("schwelle = -5.0", u.pfad.read_text())
        self.assertEqual(u.neustarts, 1)
        self.assertEqual(config.load(u.pfad)["abregelung"]["schwelle"], -5.0)

    def _abgelehnt(self, u: Umgebung, befehl: dict, muster: str):
        vorher = u.pfad.read_text()
        u.lauf([befehl])
        self.assertEqual(u.pfad.read_text(), vorher, "Datei wurde verändert")
        self.assertEqual(u.neustarts, 0)
        self.assertIsNone(u.fw.z["wartend"])
        (bid, ok, text), = u.quittungen()
        self.assertEqual((bid, ok), (befehl["id"], False))
        self.assertRegex(text, muster)
        self.assertFalse((u.d / "config-sicherung").exists())
        return text

    def test_abgelehnt_wenn_fernwartung_aus(self):
        u = Umgebung(self, fernwartung="false")
        self._abgelehnt(u, u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Neu"')), "ausgeschaltet")

    def test_abgelehnt_waehrend_abregelung(self):
        u = Umgebung(self)
        u.state.abregelung = types.SimpleNamespace(aktiv=True, modus="simulation")
        self._abgelehnt(u, u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Neu"')), "abgeregelt")
        u.srv.posts.clear()
        u.fw.verarbeiten([{"id": ID(2), "art": "update"}])
        u.fw.nachlauf()
        self.assertRegex(u.quittungen()[0][2], "abgeregelt")
        self.assertFalse((u.d / "update-anfrage").exists())

    def test_abgelehnt_ungueltig(self):
        u = Umgebung(self)
        self._abgelehnt(u, u.konfig_befehl(1, "das ist [kein toml"), "ungültig, nichts gespeichert")

    def test_abgelehnt_falsche_basis(self):
        u = Umgebung(self)
        self._abgelehnt(u, u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Neu"'), "0" * 64),
                        "inzwischen geändert")

    def test_abgelehnt_unvollstaendig(self):
        u = Umgebung(self)
        for nr, nutzlast in enumerate((None, {}, {"text": "x"}, {"text": 5, "basis": "x"}, "text",
                                       {"text": "x" * 200_001, "basis": "x"})):
            u.srv.posts.clear()
            self._abgelehnt(u, {"id": ID(nr), "art": "konfig", "nutzlast": nutzlast}, "unvollständig")

    def test_abgelehnt_gesperrte_abschnitte(self):
        for abschnitt, alt, neu in (
                ("portal", "fernwartung = true", "fernwartung = false"),
                ("portal", f'token = "{MASKE}"', 'token = "' + "f" * 48 + '"'),
                ("portal", 'schluessel = "k"', 'schluessel = "anderer"'),
                ("server", "[daten]", "[server]\nport = 8099\n\n[daten]"),
                ("daten", "[daten]", "[daten]\naufbewahrung_tage = 3"),
                ("daten", "/data.db", "/woanders.db")):
            u = Umgebung(self)
            m = maskieren(u.text)
            self.assertIn(alt, m)
            self._abgelehnt(u, u.konfig_befehl(1, m.replace(alt, neu)), rf"\[{abschnitt}\] kann nur vor Ort")

    def test_abgelehnt_abschnitt_portal_entfernt(self):
        u = Umgebung(self)
        m = maskieren(u.text)
        self._abgelehnt(u, u.konfig_befehl(1, m[:m.index("[portal]")] + m[m.index("[daten]"):]), r"\[portal\]")

    def test_abgelehnt_demo_modus(self):
        u = Umgebung(self)
        u.text = u.text.replace("[smartme]", '[quelle]\ntyp = "sunspec"\n[quelle.wechselrichter]\nhost = "192.0.2.1"\n\n[smartme]')
        u.pfad.write_text(u.text)
        m = maskieren(u.text)
        self._abgelehnt(u, u.konfig_befehl(1, m.replace('typ = "sunspec"', 'typ = "demo"')), "Demo-Modus")
        u.srv.posts.clear()
        self._abgelehnt(u, u.konfig_befehl(2, m.replace('typ = "sunspec"\n', "")), "Demo-Modus")      # Standard wäre demo
        u.srv.posts.clear()
        demo_haus = (Path(__file__).resolve().parent.parent / "pi" / "config.haus-demo.toml").read_text()
        haus = demo_haus[demo_haus.index("[haus]"):]
        self.assertIn("demo = true", haus)
        self._abgelehnt(u, u.konfig_befehl(3, m + "\n" + haus), "Demo-Modus")
        self.assertEqual(u.fw.z["quittungen"], [])

    def test_abgelehnt_adresse_geaendert_bei_uebernommenem_geheimnis(self):
        u = Umgebung(self)
        m = maskieren(u.text)
        text = self._abgelehnt(u, u.konfig_befehl(1, m.replace("https://api.smart-me.com", "https://boese.example")),
                               "Geheimnis neu eingeben")
        self.assertIn("[smartme]", text)
        u.srv.posts.clear()
        self._abgelehnt(u, u.konfig_befehl(2, m.replace('benutzer = "tin"', 'benutzer = "tin"\nbasis_url = "https://boese.example"')),
                        "Geheimnis neu eingeben")
        # mit neu eingetipptem Geheimnis ist dieselbe Änderung erlaubt
        u.srv.posts.clear()
        u.lauf([u.konfig_befehl(3, m.replace("https://api.smart-me.com", "https://api2.smart-me.com")
                                .replace(f'api_key = "{MASKE}"', 'api_key = "neuer-schluessel-99"'))])
        self.assertEqual(u.neustarts, 1)
        self.assertIn('api_key = "neuer-schluessel-99"', u.pfad.read_text())
        self.assertIn(f'passwort = "{PASSWORT}"', u.pfad.read_text())

    def test_uebernahme_pruefen_direkt(self):
        alt = '[[geraet]]\nname = "A"\ntyp = "shelly"\nhost = "10.0.0.1"\npasswort = "p-eins-1"\n\n' \
              '[[geraet]]\nname = "B"\ntyp = "shelly"\nhost = "10.0.0.2"\npasswort = "p-zwei-2"\n'
        m = maskieren(alt)

        def pruefe(gesendet):
            uebernahme_pruefen(gesendet, demaskieren(gesendet, alt), alt)

        pruefe(m)                                                                # unverändert: in Ordnung
        pruefe(m + "\n# Kommentar\n\n")
        pruefe(m.replace('host = "10.0.0.1"', 'host = "10.0.0.1"   # Kommentar dahinter'))
        with self.assertRaisesRegex(ValueError, r"\[\[geraet\]\] Nr. 2 wurde geändert; Geheimnis neu eingeben"):
            pruefe(m.replace("10.0.0.2", "10.9.9.9"))                            # zweites Gerät umgebogen
        pruefe(m.replace("10.0.0.2", "10.9.9.9").replace(f'host = "10.9.9.9"\npasswort = "{MASKE}"',
                                                         'host = "10.9.9.9"\npasswort = "neu-getippt"'))
        teile = m.split("\n\n")
        with self.assertRaisesRegex(ValueError, "Geheimnis neu eingeben"):       # Geräte vertauscht
            pruefe(teile[1].rstrip("\n") + "\n\n" + teile[0] + "\n")
        with self.assertRaisesRegex(ValueError, "Geheimnis neu eingeben"):       # erstes Gerät gelöscht
            pruefe(teile[1])
        with self.assertRaisesRegex(ValueError, "Mehrzeilige"):
            pruefe(m + "\n[anlage]\nname = \'\'\'\nX\'\'\'\n")
        with self.assertRaisesRegex(ValueError, "Mehrzeilige"):
            pruefe(m + '\n[anlage]\nname = """X"""\n')

    def test_geheimnis_wandert_nicht_in_fremdes_feld(self):
        """Belegfall des Prüfers: nachgebauter [smartme]-Abschnitt in einem mehrzeiligen Wert eines neuen Geräts."""
        u = Umgebung(self)
        m = maskieren(u.text)
        echt = f'[easee]\nbenutzer = "tin"\npasswort = "{MASKE}"\n'
        self.assertIn(echt, m)
        boese = m.replace(echt, '[[geraet]]\nname = "x"\nrolle = "verbraucher"\ntyp = "whatwatt"\n'
                          'host = "angreifer.example"\npasswort = \'\'\'\n' + echt.rstrip("\n") + "\'\'\'\n")
        self.assertIn(PASSWORT, demaskieren(boese, u.text))                       # die Zeilenerkennung fällt darauf herein
        text = self._abgelehnt(u, u.konfig_befehl(1, boese), "Mehrzeilige")
        self.assertNotIn(PASSWORT, text)
        # ohne die Sperre mehrzeiliger Werte fängt die Prüfung auf TOML-Ebene denselben Fall
        with self.assertRaisesRegex(ValueError, "kein Geheimnis erwartet|kein Wert gespeichert"):
            with mock.patch.object(konfig_web, "MEHRZEILIG", ()):
                konfig_web.uebernahme_pruefen(boese, demaskieren(boese, u.text), u.text)

    def test_abgelehnt_demo_durch_entfernen_der_geraete(self):
        """Belegfall des Prüfers: typ = "demo" steht schon da, wirksam wird es erst ohne [[geraet]]."""
        u = Umgebung(self)
        geraet = '\n[[geraet]]\nname = "WR"\nrolle = "pv"\ntyp = "sunspec"\nhost = "192.0.2.50"\n'
        u.text = u.text.replace("[smartme]", '[quelle]\ntyp = "demo"\n\n[smartme]') + geraet
        u.pfad.write_text(u.text)
        m = maskieren(u.text)
        self._abgelehnt(u, u.konfig_befehl(1, m.replace(geraet, "")), "Demo-Modus")
        u.srv.posts.clear()
        u.lauf([u.konfig_befehl(2, m.replace("192.0.2.50", "192.0.2.51"))])       # Gerät ändern bleibt erlaubt
        self.assertEqual(u.neustarts, 1)

    def test_profil_ist_nur_ein_name(self):
        """Belegfall des Prüfers: Profil als Pfad auf eine Datei mit eigenem [steuerung]-Abschnitt."""
        import os
        from solarmaa_dash.abregelung import lade_steuerprofil
        from solarmaa_dash.geraete.modbus_profil import PROFIL_DIR, lade_profil
        u = Umgebung(self)
        # der fremde Abschnitt steht schon in der Datei (z. B. von früher): es zählt allein der Profilname
        u.text += '\n[steuerung]\nlimit_register = 40001\nlimit_typ = "u16"\nfreigabe_register = 12345\nfreigabe_wert = 7\n' \
                  '\n[werte.pv_w]\nregister = 1\ntyp = "u16"\n'
        u.pfad.write_text(u.text)
        rel = os.path.relpath(u.pfad.with_suffix(""), PROFIL_DIR)
        for name in (rel, str(u.pfad), str(u.pfad.with_suffix("")), "../profile/kostal_piko_ci", "Kostal", "", "a/b", 7, None):
            with self.assertRaises(ValueError, msg=repr(name)):
                lade_steuerprofil(name)
            with self.assertRaises(ValueError, msg=repr(name)):
                lade_profil(name)
        self.assertIn("limit_register", lade_steuerprofil("kostal_piko_ci")["steuerung"])
        wr = ('\n[abregelung]\nmodus = "simulation"\npreisquelle = "keine"\n\n[[abregelung.wechselrichter]]\nname = "x"\n'
              'host = "192.0.2.1"\nnennleistung_w = 1000\nprofil = "%s"\n')
        self._abgelehnt(u, u.konfig_befehl(1, maskieren(u.text) + wr % rel), "Profilname")
        u.srv.posts.clear()
        self._abgelehnt(u, u.konfig_befehl(2, maskieren(u.text) + '\n[[geraet]]\nname = "g"\nrolle = "pv"\n'
                                           f'typ = "modbus_profil"\nhost = "192.0.2.2"\nprofil = "{u.pfad}"\n'), "Profilname")
        with self.assertRaisesRegex(ValueError, "Profilname"):                    # lokal gilt dasselbe
            u.state.konfig.speichern(maskieren(u.text) + wr % rel, "admin")

    def test_neue_unbekannte_abschnitte_werden_abgelehnt(self):
        u = Umgebung(self)
        m = maskieren(u.text)
        steuerung = '\n[steuerung]\nlimit_register = 40001\n'
        self._abgelehnt(u, u.konfig_befehl(1, m + steuerung), r"unbekannter Abschnitt \[steuerung\]")
        with self.assertRaisesRegex(ValueError, r"unbekannter Abschnitt \[steuerung\]"):
            u.state.konfig.speichern(m + steuerung, "admin")                      # auch lokal
        self.assertEqual(u.pfad.read_text(), u.text)
        # ein Abschnitt, den es in der Datei schon gibt, macht die Anlage nicht unspeicherbar
        u.text += '\n[notizen]\ntext = "bleibt"\n'
        u.pfad.write_text(u.text)
        u.srv.posts.clear()
        u.lauf([u.konfig_befehl(2, maskieren(u.text).replace('"Alt"', '"Neu"'))])
        self.assertIn('name = "Neu"', u.pfad.read_text())
        self.assertIn("[notizen]", u.pfad.read_text())

    def test_csv_pfad_nur_vor_ort_und_fehler_ohne_dateiinhalt(self):
        """Befund M1: csv_pfad auf eine fremde Datei, deren Inhalt in der Fehlermeldung (Alarm im Portal) landet."""
        from solarmaa_dash.preise import Preise, parse_csv
        u = Umgebung(self)
        m = maskieren(u.text)
        csv = f'\n[abregelung]\npreisquelle = "csv"\ncsv_pfad = "{u.pfad}"\n'
        self._abgelehnt(u, u.konfig_befehl(1, m + csv), "csv_pfad kann nur vor Ort geändert werden")
        # vor Ort gesetzt: bleibt aus der Ferne erhalten, andere Werte in [abregelung] sind änderbar, der Pfad nicht
        preise = u.d / "preise.csv"
        preise.write_text("Zeit;Preis\n2026-06-21 12:00;-4.2\n")
        u.text += f'\n[abregelung]\npreisquelle = "csv"\ncsv_pfad = "{preise}"\nschwelle = 0.0\n'
        u.pfad.write_text(u.text)
        m = maskieren(u.text)
        u.srv.posts.clear()
        self._abgelehnt(u, u.konfig_befehl(2, m.replace(str(preise), str(u.pfad))), "csv_pfad kann nur vor Ort")
        u.srv.posts.clear()
        u.lauf([u.konfig_befehl(3, m.replace("schwelle = 0.0", "schwelle = -3.0"))])
        self.assertEqual(u.neustarts, 1)
        # Fehlermeldungen nennen Zeile und Art, nie den Inhalt
        fremd = f'[easee]\npasswort = "easee,{PASSWORT}"\napi_key = "{API_KEY};x"\n'
        for text, muster in ((fremd, "Zeile 2: Zeitpunkt nicht lesbar"),
                             ("2026-06-21 12:00;-4.2\n2026-06-21 12:15;" + PASSWORT + "\n", "Zeile 2: Preis ist keine Zahl")):
            with self.assertRaisesRegex(ValueError, muster) as cm:
                parse_csv(text)
            for geheim in (API_KEY, PASSWORT, TOKEN, "smartme", "easee"):
                self.assertNotIn(geheim, str(cm.exception))
        self.assertEqual(len(parse_csv("Zeit;Preis\n2026-06-21 12:00;-4.2\n2026-06-21 12:15;1.0\n")), 2)
        (u.d / "fremd.toml").write_text(fremd)
        for datei in (u.d / "fremd.toml", u.d / "gibt-es-nicht.csv"):
            pr = Preise({"preisquelle": "csv", "csv_pfad": str(datei)}, None)
            pr.aktualisieren()
            self.assertIn("Preise nicht abrufbar: Preisdatei", pr.fehler)
            for geheim in (API_KEY, PASSWORT, TOKEN, "passwort", "api_key"):
                self.assertNotIn(geheim, pr.fehler)

    def test_hinweis_bei_eingefuegtem_eintrag(self):
        u = Umgebung(self)
        geraet = '\n[[geraet]]\nname = "A"\ntyp = "shelly"\nrolle = "netz"\nhost = "192.0.2.7"\npasswort = "geraet-passwort-1"\n'
        u.text += geraet
        u.pfad.write_text(u.text)
        m = maskieren(u.text)
        neu = m.replace("\n[[geraet]]", '\n[[geraet]]\nname = "Neu"\ntyp = "shelly"\nrolle = "pv"\nhost = "192.0.2.8"\n\n[[geraet]]', 1)
        text = self._abgelehnt(u, u.konfig_befehl(1, neu), "Geheimnis(se)? .*neu ein")
        self.assertRegex(text, "eingefügt|verschoben")
        zwei = m + '\n[[geraet]]\nname = "C"\ntyp = "shelly"\nrolle = "pv"\nhost = "192.0.2.9"\npasswort = "' + MASKE + '"\n'
        u.srv.posts.clear()
        self._abgelehnt(u, u.konfig_befehl(2, zwei), "nachfolgenden Einträge neu einzugeben")

    def test_abgelehnt_solange_freigabe_aussteht(self):
        wr = lambda letzte, fehler=None: types.SimpleNamespace(letzte_w=letzte, nenn_w=60000.0, fehler=fehler)  # noqa: E731
        abr = lambda unbekannt, *w: types.SimpleNamespace(aktiv=False, modus="auto", unbekannt=unbekannt, wr=list(w))  # noqa: E731
        for nr, zustand in enumerate((abr(True, wr(None)), abr(False, wr(60000.0), wr(30000.0)),
                                      abr(False, wr(60000.0, "Zeitüberschreitung")), abr(False, wr(0.0))), 1):
            u = Umgebung(self)
            u.state.abregelung = zustand
            self._abgelehnt(u, u.konfig_befehl(nr, maskieren(u.text).replace('"Alt"', '"Neu"')),
                            "Freigabe eines Wechselrichters steht noch aus")
            u.srv.posts.clear()
            u.lauf([{"id": ID(50 + nr), "art": "update"}])
            self.assertRegex(u.quittungen()[0][2], "Freigabe")
            self.assertFalse((u.d / "update-anfrage").exists())
        for zustand in (abr(False, wr(None)), abr(False, wr(60000.0), wr(60000.0)), abr(False)):
            u = Umgebung(self)
            u.state.abregelung = zustand                                          # freigegeben bzw. nie begrenzt
            u.lauf([u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Neu"'))])
            self.assertEqual(u.neustarts, 1)

    def test_lokales_speichern_bleibt_ohne_fernregeln(self):
        u = Umgebung(self)
        sicherung = u.state.konfig.speichern(maskieren(u.text).replace("fernwartung = true", "fernwartung = false")
                                             .replace("https://api.smart-me.com", "https://api3.smart-me.com"), "admin")
        self.assertIn("fernwartung = false", u.pfad.read_text())
        self.assertIn(f'api_key = "{API_KEY}"', u.pfad.read_text())
        self.assertEqual(sicherung.read_text(), u.text)


class AblaufTests(unittest.TestCase):
    def test_gleiche_id_nur_einmal_auch_nach_neustart(self):
        u = Umgebung(self)
        b = u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Neu"'))
        u.lauf([b])
        u.lauf([b])
        self.assertEqual(u.neustarts, 1)
        u.neu()
        u.lauf([b])                                   # dieselbe Antwort nochmals nach dem Neustart
        self.assertEqual(u.neustarts, 1)
        self.assertEqual(len(u.quittungen()), 1)
        self.assertEqual(len(list((u.d / "config-sicherung").glob("*.toml"))), 1)

    def test_zweiter_befehl_im_selben_paket_wird_abgelehnt(self):
        u = Umgebung(self)
        m = maskieren(u.text)
        u.lauf([u.konfig_befehl(1, m.replace('"Alt"', '"Eins"')), u.konfig_befehl(2, m.replace('"Alt"', '"Zwei"')),
                {"id": ID(3), "art": "update"}])
        self.assertIn('"Eins"', u.pfad.read_text())
        self.assertEqual(u.neustarts, 1)
        self.assertEqual([(q[0], q[1]) for q in u.quittungen()], [(ID(2), False), (ID(3), False)])
        self.assertRegex(u.quittungen()[0][2], "erneut senden")

    def test_siebter_befehl_in_einer_stunde_wird_abgelehnt(self):
        u = Umgebung(self)
        for nr in range(1, 8):
            u.srv.posts.clear()
            u.lauf([u.konfig_befehl(nr, "das ist [kein toml")])
            self.assertRegex(u.quittungen()[0][2], "Zu viele" if nr == 7 else "ungültig")
        u.fw.z["ausgefuehrt"] = [t - 3700 for t in u.fw.z["ausgefuehrt"]]        # eine Stunde später
        u.srv.posts.clear()
        u.lauf([u.konfig_befehl(8, "das ist [kein toml")])
        self.assertRegex(u.quittungen()[0][2], "ungültig")

    def test_unbekannter_befehl_und_zu_grosses_paket(self):
        u = Umgebung(self)
        u.lauf([{"id": ID(nr), "art": "neustart"} for nr in range(1, 9)])
        self.assertEqual(len(u.quittungen()), 5)                                  # nur die ersten fünf beachtet
        self.assertTrue(all(not ok and "Unbekannter Befehl" in text for _b, ok, text in u.quittungen()))

    def test_zustand_nicht_schreibbar_nichts_ausgefuehrt_alle_quittiert(self):
        from solarmaa_dash import fernwartung
        u = Umgebung(self)
        m = maskieren(u.text)
        with mock.patch.object(fernwartung, "_schreiben", side_effect=OSError("Platte voll")), \
                self.assertLogs("solarmaa_dash", level=logging.ERROR) as logs:
            u.fw.verarbeiten([u.konfig_befehl(1, m.replace('"Alt"', '"Eins"')), {"id": ID(2), "art": "update"},
                              {"id": ID(3), "art": "update"}])
            u.fw.nachlauf()
        self.assertEqual(u.pfad.read_text(), u.text)                              # nichts ausgeführt
        self.assertEqual(u.neustarts, 0)
        self.assertFalse((u.d / "update-anfrage").exists())
        self.assertIsNone(u.fw.z["wartend"])
        self.assertEqual([(b, ok) for b, ok, _t in u.quittungen()], [(ID(1), False), (ID(2), False), (ID(3), False)])
        self.assertTrue(all("nicht schreibbar" in t for _b, _ok, t in u.quittungen()))
        self.assertTrue(any("nicht schreibbar" in z for z in logs.output))
        u.fw.verarbeiten([u.konfig_befehl(1, m.replace('"Alt"', '"Eins"'))])      # auch später nicht doch noch
        self.assertEqual(u.pfad.read_text(), u.text)

    def test_zustand_nach_dem_speichern_nicht_schreibbar_startet_trotzdem_neu(self):
        """Befund T1: Die Konfiguration steht auf der Platte, die Markierung «gespeichert» lässt sich nicht schreiben."""
        from solarmaa_dash import fernwartung
        u = Umgebung(self)
        echt = fernwartung._schreiben

        def schreiben(datei, z):
            if z.get("wartend") and z["wartend"].get("gespeichert"):
                raise OSError("Platte voll")
            return echt(datei, z)

        with mock.patch.object(fernwartung, "_schreiben", side_effect=schreiben), \
                self.assertLogs("solarmaa_dash", level=logging.ERROR):
            u.fw.verarbeiten([u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Eins"'))])
        self.assertIn('name = "Eins"', u.pfad.read_text())
        self.assertEqual(u.neustarts, 1)                                          # Datei und Dienst fallen nicht auseinander
        self.assertEqual(u.fw.z["quittungen"], [])                                # keine falsche Fehlquittung
        self.assertFalse(json.loads((u.d / DATEI).read_text())["wartend"]["gespeichert"])
        u.neu().nachlauf()                                                        # nach dem Neustart: vorsichtig, nie «erledigt»
        (bid, ok, text), = u.quittungen()
        self.assertEqual((bid, ok), (ID(1), False))
        self.assertRegex(text, "wurde geändert.*neu laden und prüfen")

    def test_quittung_bleibt_bei_portalfehler_liegen(self):
        u = Umgebung(self, routen={})                                             # Portal antwortet mit 404
        u.lauf([u.konfig_befehl(1, "das ist [kein toml")])
        self.assertEqual(len(json.loads((u.d / DATEI).read_text())["quittungen"]), 1)
        u.srv.shutdown(); u.srv.server_close()                                    # Portal ganz weg
        u.neu().nachlauf()
        self.assertEqual(len(u.fw.z["quittungen"]), 1)
        srv, host = start_http({"POST " + ERGEBNIS: True, "POST " + KONFIG: True})
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        u.portal.url = f"http://{host}"
        u.fw.nachlauf()
        self.assertEqual([b["p_befehl"] for p, b in srv.posts if p == ERGEBNIS], [ID(1)])
        self.assertEqual(json.loads((u.d / DATEI).read_text())["quittungen"], [])
        # alte Quittungen werden nach sieben Tagen verworfen
        u.fw.z["quittungen"] = [{"id": ID(9), "ok": False, "text": "alt", "ts": time.time() - 8 * 86400}]
        u.fw.nachlauf()
        self.assertEqual(u.fw.z["quittungen"], [])
        self.assertEqual(len([1 for p, _b in srv.posts if p == ERGEBNIS]), 1)

    def test_kein_geheimnis_in_quittung_log_und_zustand(self):
        u = Umgebung(self)
        m = maskieren(u.text)
        neues = "ganz-neues-passwort-77"
        # die Prüfung nennt den falschen Wert im Fehlertext; steht dort ein Geheimnis, wird es verdeckt
        kaputt = m.replace(f'passwort = "{MASKE}"', f'passwort = "{neues}"') + f'\n[quelle]\ntyp = "{neues}"\n'
        with self.assertLogs("solarmaa_dash", level=logging.INFO) as logs:
            u.lauf([u.konfig_befehl(1, kaputt),
                    u.konfig_befehl(2, m.replace("[anlage]", f'[quelle]\ntyp = "{API_KEY}"\n[anlage]'))])
        self.assertEqual(len(u.quittungen()), 2)
        alles = json.dumps(u.srv.posts) + "\n".join(logs.output) + (u.d / DATEI).read_text() \
            + json.dumps(u.portal.status())
        self.assertIn("quelle.typ muss", alles)
        self.assertIn(MASKE, alles)
        for geheim in (neues, API_KEY, PASSWORT, TOKEN):
            self.assertNotIn(geheim, alles.replace(json.dumps(TOKEN), '"p_token"') if geheim == TOKEN else alles)
        self.assertNotIn(TOKEN, "\n".join(logs.output) + (u.d / DATEI).read_text())
        self.assertTrue(any("Fernwartung: konfig" in z for z in logs.output))


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.u = Umgebung(self)
        self.log = self.u.d / "update.log"
        p = mock.patch.object(wartung, "UPDATE_LOG", self.log)
        p.start(); self.addCleanup(p.stop)
        echt = Path.exists
        p = mock.patch.object(Path, "exists", lambda s: True if s.name == "solarmaa-dash-update.path" else echt(s))
        p.start(); self.addCleanup(p.stop)

    def _kopf(self, ts: float) -> str:
        return f"=== Update {time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(ts))}, Branch stabil ===\n"

    def _anfordern(self, nr: int = 1):
        self.u.srv.posts.clear()
        self.u.lauf([{"id": ID(nr), "art": "update", "nutzlast": None}])
        self.assertTrue((self.u.d / "update-anfrage").read_text().startswith("portal "))
        self.assertEqual(self.u.quittungen(), [])                                # keine Sofortquittung
        self.assertEqual(self.u.fw.z["wartend"]["art"], "update")

    def test_ok(self):
        self._anfordern()
        self.log.write_text(self._kopf(time.time() - 3600) + "OK: aaa → bbb installiert.\n")     # älterer Lauf
        self.u.fw.nachlauf()
        self.assertEqual(self.u.quittungen(), [])
        self.log.write_text(self.log.read_text() + self._kopf(time.time()) + "Tests laufen\n")   # läuft noch
        self.u.fw.nachlauf()
        self.assertEqual(self.u.quittungen(), [])
        self.log.write_text(self.log.read_text() + "OK: bbb → ccc installiert.\n")
        self.u.neu().nachlauf()                                                  # Dienst wurde dabei neu gestartet
        self.assertEqual(self.u.quittungen(), [(ID(1), True, "OK: bbb → ccc installiert.")])
        self.assertIsNone(self.u.fw.z["wartend"])

    def test_bereits_aktuell_fehler_verschoben(self):
        for nr, (zeilen, ok, muster) in enumerate((
                ("Bereits aktuell (abc1234).\n", True, "Bereits aktuell"),
                ("FEHLER: Tests fehlgeschlagen, zurück auf abc1234\nnoch eine Zeile\n", False, "Tests fehlgeschlagen"),
                ("Verschoben: Anlage wird gerade abgeregelt. Später erneut starten.\n", False, "Verschoben")), 1):
            self.log.write_text(self._kopf(time.time() - 60) + "OK: alt → älter installiert.\n")
            self._anfordern(nr)
            self.log.write_text(self.log.read_text() + self._kopf(time.time()) + zeilen)
            self.u.fw.nachlauf()
            (bid, ok_ist, text), = self.u.quittungen()
            self.assertEqual((bid, ok_ist), (ID(nr), ok))
            self.assertRegex(text, muster)

    def test_kein_ergebnis_nach_20_minuten(self):
        self._anfordern()
        self.u.fw.z["wartend"]["ts"] -= 19 * 60
        self.u.fw.nachlauf()
        self.assertEqual(self.u.quittungen(), [])
        self.u.fw.z["wartend"]["ts"] -= 2 * 60
        self.u.fw.nachlauf()
        self.assertEqual(self.u.quittungen(), [(ID(1), False, "Kein Ergebnis im Update-Protokoll")])

    def test_waehrend_update_kein_zweiter_befehl(self):
        self._anfordern()
        self.u.lauf([self.u.konfig_befehl(2, maskieren(self.u.text).replace('"Alt"', '"Neu"'))])
        self.assertRegex(self.u.quittungen()[0][2], "läuft noch")
        self.assertIn('"Alt"', self.u.pfad.read_text())

    def test_update_nicht_eingerichtet(self):
        u = Umgebung(self)
        with mock.patch.object(Path, "exists", lambda s: False if s.name == "solarmaa-dash-update.path" else True):
            u.fw.verarbeiten([{"id": ID(1), "art": "update"}])
        u.fw.nachlauf()
        (bid, ok, text), = u.quittungen()
        self.assertFalse(ok)
        self.assertIn("nicht eingerichtet", text)
        self.assertIsNone(u.fw.z["wartend"])


class FristenUndRueckfallTests(unittest.TestCase):
    def test_neustart_ausgeblieben(self):
        u = Umgebung(self)
        u.lauf([u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Neu"'))])
        u.fw.z["wartend"]["ts"] -= 6 * 60
        u.fw.nachlauf()
        (bid, ok, text), = u.quittungen()
        self.assertFalse(ok)
        self.assertIn("Neustart ist ausgeblieben", text)

    def test_neustart_vor_dem_speichern(self):
        u = Umgebung(self)
        zustand = {"gesehen": [ID(1)], "ausgefuehrt": [], "quittungen": [],
                   "wartend": {"id": ID(1), "art": "konfig", "ts": time.time(), "gespeichert": False, "basis": basis(u.text)}}
        (u.d / DATEI).write_text(json.dumps(zustand))
        u.neu().nachlauf()
        self.assertEqual(u.quittungen(), [(ID(1), False, "Neustart vor dem Speichern, nichts geändert")])
        u.srv.posts.clear()
        zustand["wartend"]["basis"] = "0" * 64                                    # Datei ist nicht mehr die bearbeitete
        (u.d / DATEI).write_text(json.dumps(zustand))
        u.neu().nachlauf()
        self.assertRegex(u.quittungen()[0][2], "neu laden und prüfen")

    def test_ruecksetzen_nach_fehlgeschlagenem_start(self):
        u = Umgebung(self)
        u.lauf([u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Neu"'))])
        self.assertIn('"Neu"', u.pfad.read_text())
        self.assertTrue(ruecksetzen_falls_probe(str(u.pfad), SystemExit("App-Verzeichnis nicht gefunden")))
        self.assertEqual(u.pfad.read_text(), u.text)                              # vorherige Fassung samt Geheimnissen
        self.assertFalse(ruecksetzen_falls_probe(str(u.pfad), RuntimeError("nochmals")))   # kein zweites Mal
        u.neu().nachlauf()
        (bid, ok, text), = u.quittungen()
        self.assertEqual((bid, ok), (ID(1), False))
        self.assertIn("fehlgeschlagen (SystemExit). Vorherige Fassung wiederhergestellt", text)

    def test_ruecksetzen_ohne_wartenden_befehl_tut_nichts(self):
        u = Umgebung(self)
        self.assertFalse(ruecksetzen_falls_probe(str(u.pfad), RuntimeError("x")))
        self.assertFalse(ruecksetzen_falls_probe(None, RuntimeError("x")))
        (u.d / DATEI).write_text("kein json")
        self.assertFalse(ruecksetzen_falls_probe(str(u.pfad), RuntimeError("x")))
        # Sicherung ausserhalb des Sicherungsordners wird nie zurückgespielt
        fremd = u.d / "fremd.toml"
        fremd.write_text("[anlage]\nname = 'fremd'\n")
        (u.d / DATEI).write_text(json.dumps({"wartend": {"id": ID(1), "art": "konfig", "ts": 1, "gespeichert": True,
                                                         "sicherung": str(fremd)}}))
        self.assertFalse(ruecksetzen_falls_probe(str(u.pfad), RuntimeError("x")))
        # Update wartet: Konfiguration bleibt
        (u.d / DATEI).write_text(json.dumps({"wartend": {"id": ID(1), "art": "update", "ts": 1}}))
        self.assertFalse(ruecksetzen_falls_probe(str(u.pfad), RuntimeError("x")))
        self.assertEqual(u.pfad.read_text(), u.text)

    def test_dienst_startet_mit_kaputter_fernkonfiguration_nicht_und_setzt_zurueck(self):
        from solarmaa_dash.__main__ import cmd_run
        u = Umgebung(self)
        u.lauf([u.konfig_befehl(1, maskieren(u.text).replace('"Alt"', '"Neu"'))])
        u.pfad.write_text(u.pfad.read_text() + "\n[server]\napp_pfad = \"/gibt/es/nicht\"\nport = 0\nhost = \"127.0.0.1\"\n")
        args = types.SimpleNamespace(config=str(u.pfad), db=None, port=None)
        with mock.patch("solarmaa_dash.__main__.serve", side_effect=RuntimeError("Start scheitert")):
            self.assertEqual(cmd_run(args), 1)
        self.assertEqual(u.pfad.read_text(), u.text)
        with mock.patch("solarmaa_dash.__main__.serve", side_effect=RuntimeError("Start scheitert")):
            with self.assertRaisesRegex(RuntimeError, "Start scheitert"):            # ohne Fernbefehl: Fehler wie bisher
                cmd_run(args)


class PortalVerbindenTests(unittest.TestCase):
    """pi/portal-verbinden.sh: der eingebettete Python-Teil, der den [portal]-Block schreibt."""

    def test_block_enthaelt_fernwartung(self):
        import subprocess
        skript = (Path(__file__).resolve().parent.parent / "pi" / "portal-verbinden.sh").read_text(encoding="utf-8")
        teil = skript.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        self.assertIn("[portal]", teil)
        for vorher in ('[anlage]\nname = "X"\n',
                       '[anlage]\nname = "X"\n\n[portal]\nurl = "https://alt.example"\ntoken = "alt"\n\n[server]\nport = 80\n'):
            pfad = Path(tempfile.mkdtemp()) / "config.toml"
            pfad.write_text(vorher, encoding="utf-8")
            subprocess.run([sys.executable, "-", str(pfad), "https://x.supabase.co", "sb_publishable_x", "a" * 48],
                           input=teil, text=True, check=True)
            cfg = config.load(pfad)
            self.assertIs(cfg["portal"]["fernwartung"], True)
            self.assertEqual(cfg["portal"]["token"], "a" * 48)
            self.assertEqual(pfad.read_text().count("[portal]"), 1)
            self.assertTrue(Portal(cfg).fernwartung)
            self.assertIn("# Fernwartung durch Solarmaa (auf false setzen, wenn nicht vereinbart)\nfernwartung = true\n",
                          pfad.read_text())
        # ein bewusst gesetztes false bleibt beim erneuten Verbinden erhalten
        for false_zeile in ("fernwartung = false", "  fernwartung=false   # nicht vereinbart"):
            pfad = Path(tempfile.mkdtemp()) / "config.toml"
            pfad.write_text(f'[anlage]\nname = "X"\n\n[portal]\nurl = "https://alt.example"\n{false_zeile}\ntoken = "alt"\n'
                            '\n[server]\nport = 80\n', encoding="utf-8")
            subprocess.run([sys.executable, "-", str(pfad), "https://x.supabase.co", "sb_publishable_x", "a" * 48],
                           input=teil, text=True, check=True)
            cfg = config.load(pfad)
            self.assertIs(cfg["portal"]["fernwartung"], False)
            self.assertEqual(cfg["portal"]["token"], "a" * 48)
            self.assertEqual(cfg["server"]["port"], 80)


class AdressenTests(unittest.TestCase):
    """Befund B4: urllib öffnet auch file:// und ftp://; Adressen aus der Konfiguration nur http(s)."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.geheim = self.d / "geheim.json"
        self.geheim.write_text('{"Devices": [{"Id": "lokal"}], "passwort": "' + PASSWORT + '"}')

    def _laden(self, toml: str, streng: bool = True):
        pfad = self.d / "c.toml"
        pfad.write_text(toml, encoding="utf-8")
        return config.load(pfad, streng=streng)

    def test_konfiguration_lehnt_fremde_schemata_ab(self):
        datei = self.geheim.as_uri()
        for abschnitt, feld, gut in (("[smartme]", "basis_url", ["https://api.smart-me.com"]),
                                     ("[easee]", "basis_url", ["https://api.easee.com"]),
                                     ("[benachrichtigung.push]", "server", ["https://ntfy.sh", "http://ntfy.intern"]),
                                     ("[benachrichtigung]", "lebenszeichen_url", ["https://hc-ping.com/abc", "http://192.0.2.4/ping"])):
            for schlecht in (datei, f"{datei}#/Devices", "ftp://192.0.2.1/x", "https://", "http:///etc/passwd", "gopher://x",
                             "api.smart-me.com", "HTTPS"):
                with self.assertRaisesRegex(ValueError, feld, msg=f"{abschnitt} {feld} = {schlecht}") as cm:
                    self._laden(f'{abschnitt}\n{feld} = "{schlecht}"\n')
                if len(schlecht) > 6:
                    self.assertNotIn(schlecht, str(cm.exception))                     # Wert wird nie wiedergegeben
                # eine bestehende Datei mit diesem Wert startet trotzdem, mit Warnung
                cfg = self._laden(f'{abschnitt}\n{feld} = "{schlecht}"\n', streng=False)
                self.assertTrue(any(feld in w for w in cfg["_warnungen"]))
                self.assertFalse(any(len(schlecht) > 6 and schlecht in w for w in cfg["_warnungen"]))
            for ok in gut:
                self._laden(f'{abschnitt}\n{feld} = "{ok}"\n')
            self._laden(f'{abschnitt}\n{feld} = ""\n')                     # leer = nicht benutzt
        for nur_https in ("smartme", "easee"):
            with self.assertRaisesRegex(ValueError, "https://"):
                self._laden(f'[{nur_https}]\nbasis_url = "http://api.example"\n')
        self._laden("")                                                          # Standardwerte sind gültig

    def test_fern_und_lokal_abgelehnt(self):
        u = Umgebung(self)
        m = maskieren(u.text)
        boese = m.replace("https://api.smart-me.com", self.geheim.as_uri() + "#")
        text = self._abgelehnt(u, boese)
        self.assertIn("basis_url", text)
        with self.assertRaisesRegex(ValueError, "basis_url"):
            u.state.konfig.speichern(boese, "admin")
        self.assertEqual(u.pfad.read_text(), u.text)

    def _abgelehnt(self, u, text):
        u.lauf([u.konfig_befehl(1, text)])
        (bid, ok, t), = u.quittungen()
        self.assertFalse(ok)
        self.assertEqual(u.neustarts, 0)
        return t

    def test_aufrufstellen_oeffnen_keine_datei(self):
        from solarmaa_dash.benachrichtigung import Alarme
        from solarmaa_dash.geraete import GeraetFehler
        from solarmaa_dash.geraete.easee import EaseeKonto
        from solarmaa_dash.geraete.http import get_json, get_raw
        from solarmaa_dash.geraete.smartme import SmartMeKonto
        datei = self.geheim.as_uri()
        for f in (get_json, get_raw):
            with self.assertRaisesRegex(GeraetFehler, "http"):
                f(datei + "#/Devices")
        with self.assertRaises(Exception) as cm:
            SmartMeKonto(datei + "#", "k", None, None).geraete()
        self.assertNotIn("lokal", str(cm.exception))
        with self.assertRaisesRegex(Exception, "basis_url"):
            EaseeKonto("a@b.ch", "x", datei + "#").zustand("EH123")
        # Push auf file: gilt nicht als versendet, sondern als Fehler
        a = Alarme({"anlage": {"name": "T"}, "benachrichtigung": {"push": {"server": datei + "#", "thema": "t"}}})
        with self.assertRaisesRegex(Exception, "Push: .*push.server"):
            a._senden("Titel", "Text", "warnung")

    def test_portal_url_genau_pruefen(self):
        basis = {"schluessel": "k", "token": TOKEN}
        for gut in ("https://abc.supabase.co", "http://127.0.0.1:8090", "http://localhost:8090"):
            Portal(config._merge(config.DEFAULTS, {"portal": {**basis, "url": gut}}))
        for schlecht in ("http://boese.example/?127.0.0.1", "http://127.0.0.1.boese.example", "http://boese.example#127.0.0.1",
                         "file:///etc/passwd?127.0.0.1", "ftp://127.0.0.1", "https://", "http://unsicher.example"):
            with self.assertRaisesRegex(ValueError, "portal.url", msg=schlecht):
                Portal(config._merge(config.DEFAULTS, {"portal": {**basis, "url": schlecht}}))


class StartUndUpdateTests(unittest.TestCase):
    """Befund M2: bisher startfähige Werte, verständliche Meldung beim Start, Prüfung vor dem Update."""
    PI = Path(__file__).resolve().parent.parent / "pi"

    def _datei(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "c.toml").write_text(text, encoding="utf-8")
        return d / "c.toml"

    def _aufruf(self, *args):
        import subprocess
        return subprocess.run([sys.executable, "-m", "solarmaa_dash", *args], cwd=self.PI, capture_output=True,
                              text=True, timeout=60, env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_bisher_gueltige_werte_bleiben_gueltig(self):
        for text in ('[benachrichtigung]\nlebenszeichen_url = false\n', '[benachrichtigung]\nlebenszeichen_url = ""\n',
                     '[benachrichtigung.push]\nserver = ""\nthema = "t"\n', '[benachrichtigung.push]\nserver = false\n',
                     '[smartme]\nbasis_url = ""\n', '[easee]\nbasis_url = "  "\n'):
            self.assertNotIn("_warnungen", config.load(self._datei(text), streng=True))
        ntfy = '[benachrichtigung]\nlebenszeichen_url = "hc-ping.com/abc-geheim"\n[benachrichtigung.push]\nserver = "ntfy.sh"\nthema = "t"\n'
        with self.assertRaisesRegex(ValueError, "push.server: Adresse ohne Schema, bitte https:// voranstellen") as cm:
            config.load(self._datei(ntfy), streng=True)                           # Speichern: abgelehnt
        self.assertNotIn("ntfy.sh", str(cm.exception))
        cfg = config.load(self._datei(ntfy))                                      # Start: kein Abbruch
        self.assertEqual(cfg["benachrichtigung"]["push"], {})                      # Push aus, nicht an ntfy.sh
        self.assertIsNone(cfg["benachrichtigung"]["lebenszeichen_url"])
        self.assertEqual(len(cfg["_warnungen"]), 2)
        self.assertNotIn("ntfy.sh", " ".join(cfg["_warnungen"]))
        self.assertNotIn("abc-geheim", " ".join(cfg["_warnungen"]))
        with self.assertRaisesRegex(ValueError, "lebenszeichen_url"):
            config.load(self._datei('[benachrichtigung]\nlebenszeichen_url = true\n'), streng=True)

    def test_warnung_wird_beim_start_alarm(self):
        from solarmaa_dash import __main__ as hauptprogramm
        d = Path(tempfile.mkdtemp())
        pfad = self._datei('[benachrichtigung.push]\nserver = "ntfy.sh"\nthema = "t"\n'
                           f'[smartme]\nbasis_url = "http://smartme.example"\n[daten]\npfad = "{d}/data.db"\n')
        args = types.SimpleNamespace(config=str(pfad), db=None, port=0)
        with mock.patch.object(hauptprogramm, "serve"), mock.patch.object(hauptprogramm, "find_app_dir"), \
                self.assertLogs("solarmaa_dash", level=logging.WARNING) as logs:
            cfg, _store, _haus, _source, state, _httpd = hauptprogramm._aufbauen(args)
        offen = state.alarme.status()["offen"]
        self.assertEqual([a["key"] for a in offen], ["konfiguration"])
        self.assertIn("push.server", offen[0]["text"])
        self.assertIn("smartme.basis_url muss mit https:// beginnen", offen[0]["text"])
        self.assertNotIn("ntfy.sh", offen[0]["text"])
        self.assertTrue(any("Konfiguration:" in z for z in logs.output))
        self.assertTrue(any(a.get("text") == offen[0]["text"] for a in state.alarme.status()["offen"]))  # geht so ans Portal
        # smart-me mit http:// meldet beim Lesen einen Gerätefehler statt den Start abzubrechen
        from solarmaa_dash.geraete import GeraetFehler
        from solarmaa_dash.geraete.smartme import konto
        with self.assertRaisesRegex(GeraetFehler, "smartme.basis_url muss mit https://"):
            konto(cfg["smartme"])
        from solarmaa_dash.geraete.easee import konto as easee_konto
        with self.assertRaisesRegex(GeraetFehler, "easee.basis_url muss mit https://"):
            easee_konto({"benutzer": "a", "passwort": "b", "basis_url": "http://easee.example"})

    def test_push_ohne_server_nimmt_standard(self):
        from solarmaa_dash.benachrichtigung import Alarme
        a = Alarme({"anlage": {"name": "T"}, "benachrichtigung": {"push": {"server": "", "thema": "t"}}})
        with mock.patch("urllib.request.urlopen") as u:
            a._senden("Titel", "Text", "warnung")
        self.assertEqual(u.call_args[0][0].full_url, "https://ntfy.sh/t")

    def test_start_mit_ungueltiger_konfiguration_ohne_traceback(self):
        pfad = self._datei('[quelle]\ntyp = "gibtsnicht"\n[daten]\npfad = ":memory:"\n')
        r = self._aufruf("run", "-c", str(pfad), "--port", "0")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Konfiguration ungültig, Dienst startet nicht: quelle.typ muss", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        r = self._aufruf("run", "-c", str(self._datei("das ist [kein toml")), "--port", "0")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_unerwarteter_fehler_mit_traceback_aber_ohne_abbruchkaskade(self):
        """T-1: KeyError/TypeError sind kein «reiner Konfigurationsfehler»: Traceback ins Journal, Exit 2."""
        from solarmaa_dash import __main__ as hauptprogramm
        pfad = self._datei('[anlage]\nname = "X"\n[daten]\npfad = ":memory:"\n')
        args = types.SimpleNamespace(config=str(pfad), db=None, port=0)
        for fehler in (KeyError("rolle"), TypeError("float() argument must be a string")):
            with mock.patch.object(hauptprogramm, "_aufbauen", side_effect=fehler), \
                    self.assertLogs("solarmaa_dash", level=logging.ERROR) as logs:
                self.assertEqual(hauptprogramm.cmd_run(args), 2)
            text = "\n".join(logs.output)
            self.assertIn("unerwarteter Fehler " + type(fehler).__name__, text)
            self.assertIn("Traceback", text)
            self.assertNotIn("Konfiguration ungültig", text)

    def test_konfig_pruefen(self):
        r = self._aufruf("konfig-pruefen", "-c", str(self._datei('[anlage]\nname = "X"\n[smartme]\napi_key = "geheim-123"\n')))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "Konfiguration passt."))
        # was der Dienst beim Start nur als Warnung meldet, blockiert das Update nicht
        r = self._aufruf("konfig-pruefen", "-c", str(self._datei('[benachrichtigung.push]\nserver = "ntfy.sh"\nthema = "geheim"\n')))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self._aufruf("konfig-pruefen", "-c", str(self._datei('[quelle]\ntyp = "gibtsnicht"\n')))
        self.assertEqual(r.returncode, 1)
        self.assertIn("quelle.typ muss", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        r = self._aufruf("konfig-pruefen", "-c", "/gibt/es/nicht.toml")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)

    def test_konfig_pruefen_gibt_eine_druckbare_zeile_aus(self):
        """T-2: Steuerzeichen und Zeilenumbrüche aus der Meldung gelangen nicht ins Update-Protokoll."""
        import contextlib
        import io
        from solarmaa_dash import __main__ as hauptprogramm
        err = io.StringIO()
        with mock.patch("solarmaa_dash.konfig_web.pruefen", side_effect=ValueError("a\x85b\nOK: x\u2028y\x1b[31mz\rq")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(hauptprogramm.cmd_konfig_pruefen(types.SimpleNamespace(config=str(self._datei("")))), 1)
        ausgabe = err.getvalue()
        self.assertEqual(ausgabe.count("\n"), 1)
        self.assertTrue(ausgabe.rstrip("\n").isprintable(), repr(ausgabe))
        # und update_ergebnis teilt nur an \n: ein \x85 o. ä. im Protokoll erzeugt keine eigene «OK:»-Zeile
        log = Path(tempfile.mkdtemp()) / "update.log"
        log.write_text(f"=== Update {time.strftime('%d.%m.%Y %H:%M:%S')}, Branch stabil ===\n"
                       "FEHLER: Konfiguration passt nicht zur neuen Version: a\x85OK: alles gut\u2028OK: x\n", encoding="utf-8")
        with mock.patch.object(wartung, "UPDATE_LOG", log):
            ok, text = wartung.Wartung(config.load(None), log.parent).update_ergebnis(time.time() - 60)
        self.assertFalse(ok)
        self.assertTrue(text.startswith("FEHLER: Konfiguration passt nicht"))

    def test_update_sh_prueft_vor_dem_umschalten(self):
        """update.sh selbst braucht root und feste Pfade (/opt, /etc, /var/log); geprüft wird hier, dass die
        Prüfung vor install.sh steht, bei Fehler zurückschaltet, und dass ihre Meldung als Fehler zählt."""
        skript = (self.PI / "update.sh").read_text(encoding="utf-8")
        pruef, install = skript.index("konfig-pruefen -c /etc/solarmaa-dash/config.toml"), skript.index('if "$SRC/pi/install.sh" --update')
        self.assertLess(skript.index("unittest discover"), pruef)
        self.assertLess(pruef, install)
        zweig = skript[pruef:install]
        self.assertIn('echo "FEHLER: Konfiguration passt nicht zur neuen Version:', zweig)
        self.assertIn('checkout --quiet --force "$ALT"; exit 1', zweig)
        self.assertIn('install.sh" --update && dienst_antwortet; then', skript)
        zeile = skript[skript.rindex("\n", 0, pruef):pruef]
        self.assertIn("runuser -u solarmaa-dash", zeile)                         # als Dienstbenutzer, nicht als root
        log = Path(tempfile.mkdtemp()) / "update.log"
        log.write_text(f"=== Update {time.strftime('%d.%m.%Y %H:%M:%S')}, Branch stabil ===\n"
                       "FEHLER: Konfiguration passt nicht zur neuen Version: smartme.basis_url muss mit https:// beginnen. "
                       "Alte Version abc1234 bleibt.\n")
        with mock.patch.object(wartung, "UPDATE_LOG", log):
            ok, text = wartung.Wartung(config.load(None), log.parent).update_ergebnis(time.time() - 60)
        self.assertFalse(ok)
        self.assertIn("passt nicht zur neuen Version", text)

    def _update_sh_python(self, kennung: str) -> str:
        """Ein eingebetteter Python-Teil aus update.sh, mit Konfigurationspfad auf eine Testdatei umgebogen."""
        import re
        skript = (self.PI / "update.sh").read_text(encoding="utf-8")
        teile = re.findall(r"python3 - <<'PY'\n(.*?)\nPY\n", skript, re.S)
        teil = next(t for t in teile if kennung in t)
        self.assertEqual(teil.count('open("/etc/solarmaa-dash/config.toml", "rb")'), 1)
        return teil.replace("time.sleep(2)", "time.sleep(0.05)")

    def _attrappe(self, host: str, routes: dict):
        from test_erweiterung import FakeHttpHandler
        from http.server import ThreadingHTTPServer
        import threading
        srv = ThreadingHTTPServer((host, 0), FakeHttpHandler)
        srv.daemon_threads = True
        srv.routes, srv.need_key, srv.posts, srv.gets = routes, None, [], 0
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv

    def _lauf(self, teil: str, server_toml: str) -> int:
        import subprocess
        pfad = self._datei(server_toml)
        return subprocess.run([sys.executable, "-c", teil.replace("/etc/solarmaa-dash/config.toml", str(pfad))],
                              timeout=120).returncode

    def test_dienst_antwortet_im_update_sh(self):
        """M-1: dienst_antwortet() fragt die Adresse ab, an die der Dienst bindet (server.host)."""
        teil = self._update_sh_python("/api/health")
        srv = self._attrappe("127.0.0.2", {"/api/health": {"status": "ok"}})
        port = srv.server_address[1]
        self.assertEqual(self._lauf(teil, f'[server]\nhost = "127.0.0.2"\nport = {port}\n'), 0)   # gebunden an 127.0.0.2
        srv.routes.clear()
        self.assertEqual(self._lauf(teil, f'[server]\nhost = "127.0.0.2"\nport = {port}\n'), 0)   # 404/503 = läuft
        lokal = self._attrappe("127.0.0.1", {"/api/health": {"status": "ok"}})
        for host in ("", "0.0.0.0", "::"):
            self.assertEqual(self._lauf(teil, f'[server]\nhost = "{host}"\nport = {lokal.server_address[1]}\n'), 0, host)
        self.assertEqual(self._lauf(teil, f'[server]\nport = {lokal.server_address[1]}\n'), 0)
        frei = self._attrappe("127.0.0.3", {}).server_address[1]
        self.assertEqual(self._lauf(teil, f'[server]\nhost = "127.0.0.2"\nport = {frei}\n'), 1)     # niemand da

    def test_abregelungsabfrage_im_update_sh(self):
        """M-1: Die Abfrage «wird gerade abgeregelt?» vor dem Update nimmt dieselbe Adresse."""
        teil = self._update_sh_python("/api/now")
        aktiv = {"abregelung": {"aktiv": True, "modus": "auto"}}
        srv = self._attrappe("127.0.0.2", {"/api/now": aktiv})
        self.assertEqual(self._lauf(teil, f'[server]\nhost = "127.0.0.2"\nport = {srv.server_address[1]}\n'), 0)   # verschieben
        srv.routes["/api/now"] = {"abregelung": {"aktiv": False, "modus": "auto"}}
        self.assertEqual(self._lauf(teil, f'[server]\nhost = "127.0.0.2"\nport = {srv.server_address[1]}\n'), 1)
        lokal = self._attrappe("127.0.0.1", {"/api/now": aktiv})
        self.assertEqual(self._lauf(teil, f'[server]\nhost = "0.0.0.0"\nport = {lokal.server_address[1]}\n'), 0)


class HochladenTests(unittest.TestCase):
    def test_maskiert_und_nur_bei_aenderung(self):
        u = Umgebung(self)
        u.fw.nachlauf()
        u.fw.nachlauf()
        (b,) = u.gesendet(KONFIG)
        self.assertEqual(b["p_token"], TOKEN)
        self.assertEqual(b["p_text"], maskieren(u.text))
        self.assertNotIn(API_KEY, b["p_text"])
        self.assertNotIn(TOKEN, b["p_text"])
        u.pfad.write_text(u.text.replace('"Alt"', '"Lokal geändert"'))
        u.fw.nachlauf()
        self.assertEqual(len(u.gesendet(KONFIG)), 2)
        self.assertIn("Lokal geändert", u.gesendet(KONFIG)[1]["p_text"])

    def test_fernwartung_aus_schickt_einmal_leer(self):
        u = Umgebung(self, fernwartung="false")
        u.fw.nachlauf()
        u.fw.nachlauf()
        self.assertEqual([b["p_text"] for b in u.gesendet(KONFIG)], [""])

    def test_alte_datenbank_schaltet_ohne_fehlerstatus_ab(self):
        u = Umgebung(self, routen={})
        with self.assertLogs("solarmaa_dash", level=logging.INFO) as logs:
            u.fw.nachlauf()
            u.fw.nachlauf()
        self.assertEqual(len(u.gesendet(KONFIG)), 1)
        self.assertIsNone(u.portal.fehler)
        self.assertEqual(len([z for z in logs.output if "ältere Datenbank" in z]), 1)
        u.fw._konfig_pause_bis = time.time() - 1                                  # eine Stunde später
        u.fw.nachlauf()
        self.assertEqual(len(u.gesendet(KONFIG)), 2)

    def test_unerkanntes_geheimnis_verhindert_hochladen(self):
        u = Umgebung(self)
        u.pfad.write_text(u.text.replace(f'passwort = "{PASSWORT}"', f"passwort = '{PASSWORT}'"))
        with self.assertLogs("solarmaa_dash", level=logging.WARNING) as logs:
            u.fw.nachlauf()
            u.fw.nachlauf()
        self.assertEqual(u.gesendet(KONFIG), [])
        self.assertEqual(len(logs.output), 1)                                     # nur einmal warnen
        self.assertNotIn(PASSWORT, logs.output[0])
        self.assertIn("nicht ans Portal übertragen", u.portal.status()["konfig_hinweis"])

    def test_ohne_portal_passiert_nichts(self):
        u = Umgebung(self)
        u.portal.aktiv = False
        u.fw.z["quittungen"] = [{"id": ID(1), "ok": True, "text": "x", "ts": time.time()}]
        u.fw.nachlauf()
        self.assertEqual(u.srv.posts, [])


if __name__ == "__main__":
    unittest.main()
