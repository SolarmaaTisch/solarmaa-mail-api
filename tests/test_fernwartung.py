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
from solarmaa_dash.konfig_web import MASKE, KonfigWeb, demaskieren, fern_pruefen, maskieren, sicher_maskiert  # noqa: E402
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

    def test_fern_pruefen_direkt(self):
        alt = '[[geraet]]\nname = "A"\ntyp = "shelly"\nhost = "10.0.0.1"\npasswort = "p-eins-1"\n\n' \
              '[[geraet]]\nname = "B"\ntyp = "shelly"\nhost = "10.0.0.2"\npasswort = "p-zwei-2"\n'
        cfg = config._merge(config.DEFAULTS, {})
        m = maskieren(alt)
        fern_pruefen(m, alt, cfg, cfg)                                           # unverändert: in Ordnung
        fern_pruefen(m + "\n# Kommentar\n\n", alt, cfg, cfg)
        with self.assertRaisesRegex(ValueError, "Geheimnis neu eingeben"):       # zweites Gerät umgebogen
            fern_pruefen(m.replace("10.0.0.2", "10.9.9.9"), alt, cfg, cfg)
        fern_pruefen(m.replace("10.0.0.2", "10.9.9.9").replace(f'"B"\ntyp = "shelly"\nhost = "10.9.9.9"\npasswort = "{MASKE}"',
                                                               '"B"\ntyp = "shelly"\nhost = "10.9.9.9"\npasswort = "neu-getippt"'),
                     alt, cfg, cfg)
        with self.assertRaisesRegex(ValueError, "Demo-Modus"):
            fern_pruefen("", "", config._merge(cfg, {"haus": {"demo": True}}), cfg)
        fern_pruefen("", "", config._merge(cfg, {"haus": {"demo": True}}), config._merge(cfg, {"haus": {"demo": True}}))

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
