"""Ablauf Pi <-> Portal mit dem echten Pi-Code gegen die Attrappe (wird von run.sh gestartet).

  PGHOST=… PGPORT=… PGDATABASE=… python3 pi_ablauf.py http://127.0.0.1:<port> [--alt]
  python3 pi_ablauf.py http://127.0.0.1:<port> --runde <ordner> <token>

Mit --runde meldet sich ein Pi (Konfiguration in <ordner>, wird beim ersten Mal angelegt) genau
einmal beim Portal und führt dessen Befehle aus. Jeder Aufruf ist ein neuer Prozess und wirkt
darum wie ein Neustart des Dienstes; ein Update ist dort «nicht eingerichtet» (wie in der Demo).
Der Browsertest und der Tester treiben damit den Pi von Hand.

Simuliert sind nur der Neustart des Dienstes und der Update-Lauf (update.sh). Mit --alt läuft
der neue Pi gegen eine Datenbank auf dem Stand von Stufe 1 (ohne Fernwartung).
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "pi"))

from solarmaa_dash import config, wartung  # noqa: E402
from solarmaa_dash.fernwartung import Fernwartung  # noqa: E402
from solarmaa_dash.konfig_web import KonfigWeb  # noqa: E402
from solarmaa_dash.portal import Portal, portal_loop  # noqa: E402
from solarmaa_dash.server import State  # noqa: E402
from solarmaa_dash.sources import DemoSource  # noqa: E402
from solarmaa_dash.store import Store  # noqa: E402

URL = sys.argv[1]
ALT = "--alt" in sys.argv
CHEF = "00000000-0000-0000-0000-000000000001"
API_KEY = "ablauf-smartme-schluessel-1"
PASSWORT = "ablauf-easee-passwort-2"


def ok(was: str, bedingung, detail="") -> None:
    if not bedingung:
        print(f"FEHLGESCHLAGEN: {was} {detail}")
        sys.exit(1)
    print(f"ok - {was}")


def sql(stmt: str, aal: str | None = None) -> str:
    """Direkt in der Testdatenbank; mit aal als angemeldetes Solarmaa-Konto (Regeln gelten)."""
    pre = ""
    if aal:
        claims = json.dumps({"sub": CHEF, "aal": aal})
        pre = f"select set_config('request.jwt.claims', '{claims}', false); set role authenticated; "
    r = subprocess.run(["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-c", pre + stmt],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip().split("\n")[-1]


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


class EineRunde:
    """Ersatz für das Stopp-Ereignis: lässt portal_loop genau eine Runde laufen."""
    fertig = False

    def is_set(self):
        return self.fertig

    def wait(self, _t=None):
        self.fertig = True
        return True


class Pi:
    def __init__(self, token: str, ordner: str | None = None):
        self.d = Path(ordner) if ordner else Path(tempfile.mkdtemp(prefix="smpi."))
        self.d.mkdir(parents=True, exist_ok=True)
        self.pfad = self.d / "config.toml"
        if not self.pfad.exists():
            self.pfad.write_text(
                f'[anlage]\nname = "Ablauf Alt"\n\n[smartme]\napi_key = "{API_KEY}"\n\n[easee]\nbenutzer = "tin"\n'
                f'passwort = "{PASSWORT}"\n\n[portal]\nurl = "{URL}"\nschluessel = "test-key"\ntoken = "{token}"\n'
                f'fernwartung = true\n\n[daten]\npfad = "{self.d}/data.db"\n', encoding="utf-8")
        self.neustarts = 0
        self.update = "ok"
        wartung.UPDATE_LOG = self.d / "update.log"
        self.starten()

    def starten(self) -> None:
        """Wie ein (Neu-)Start des Dienstes: Konfiguration laden, alles neu aufbauen."""
        cfg = config.load(self.pfad)
        store = Store(":memory:")
        self.state = State(cfg, store, "demo")
        snap = DemoSource().read()
        store.add(snap)
        self.state.publish(snap)
        self.state.konfig = KonfigWeb(str(self.pfad), self.d)
        self.state.konfig.neustart = self._neustart
        self.state.wartung = wartung.Wartung(cfg, self.d)
        self.state.wartung.update_anfordern = self._update
        self.state.portal = Portal(cfg)
        self.state.fernwartung = Fernwartung(self.state.portal, self.state, self.d)

    def _neustart(self, *_a) -> None:
        self.neustarts += 1

    def _update(self, von: str) -> None:
        if self.update == "fehlt":
            raise ValueError("Web-Update ist auf diesem Gerät nicht eingerichtet (install.sh erneut ausführen)")
        kopf = f"=== Update {time.strftime('%d.%m.%Y %H:%M:%S')}, Branch stabil ===\n"
        wartung.UPDATE_LOG.write_text(kopf + "OK: abc1234 → def5678 installiert.\n", encoding="utf-8")

    def runde(self) -> None:
        portal_loop(self.state.portal, self.state, EineRunde())


def anlage(name: str, aal: str) -> tuple[str, str]:
    sql(f"insert into public.admins (user_id) values ('{CHEF}') on conflict do nothing")
    r = json.loads(sql(f"select public.anlage_anlegen({q(name)})", aal))
    return r["id"], r["token"]


def befehl(a_id: str, art: str, nutzlast: dict | None = None) -> str:
    arg = f", {q(json.dumps(nutzlast))}::jsonb" if nutzlast is not None else ""
    return sql(f"select public.befehl_anlegen('{a_id}', '{art}'{arg})", "aal2")


def stand(b_id: str) -> tuple[str, str]:
    r = json.loads(sql(f"select jsonb_build_array(status, coalesce(ergebnis, ''), nutzlast is null) "
                       f"from public.befehle where id = '{b_id}'"))
    if not r[2]:
        ok("Nutzlast nach der Abholung gelöscht", r[0] == "offen", str(r))
    return r[0], r[1]


def ablauf_neu() -> None:
    a_id, token = anlage("Ablauf", "aal2")
    pi = Pi(token)
    pi.runde()
    ok("Pi meldet sich, kein Fehler", pi.state.portal.fehler is None and pi.state.portal.letzter_erfolg,
       str(pi.state.portal.fehler))
    ok("Portal kennt den Schalter Fernwartung", sql(f"select fernwartung from public.anlagen where id = '{a_id}'") == "t")
    konfig = sql(f"select to_jsonb(text) from public.anlagen_konfig where anlage_id = '{a_id}'", "aal2")
    konfig = json.loads(konfig)
    ok("maskierte Konfiguration liegt im Portal", "********" in konfig and 'name = "Ablauf Alt"' in konfig)
    ok("kein Geheimnis im Portal", not any(g in konfig for g in (API_KEY, PASSWORT, token)))
    basis = hashlib.sha256(konfig.encode()).hexdigest()         # wie portal.js: über den geladenen Text

    # Konfiguration ändern
    b1 = befehl(a_id, "konfig", {"text": konfig.replace("Ablauf Alt", "Ablauf Neu"), "basis": basis})
    pi.runde()
    ok("Befehl abgeholt, noch nicht quittiert", stand(b1)[0] == "gesendet", str(stand(b1)))
    ok("Konfiguration gespeichert, Geheimnisse erhalten",
       'name = "Ablauf Neu"' in pi.pfad.read_text() and f'passwort = "{PASSWORT}"' in pi.pfad.read_text())
    ok("Dienst wird neu gestartet", pi.neustarts == 1)
    pi.starten()
    pi.runde()
    status, text = stand(b1)
    ok("nach dem Neustart quittiert: erledigt", status == "erledigt" and "neuer Konfiguration" in text, f"{status}: {text}")
    konfig2 = json.loads(sql(f"select to_jsonb(text) from public.anlagen_konfig where anlage_id = '{a_id}'", "aal2"))
    ok("Portal zeigt die neue Konfiguration", 'name = "Ablauf Neu"' in konfig2 and PASSWORT not in konfig2)

    # Abgelehnt: gesperrter Abschnitt, veralteter Stand
    basis2 = hashlib.sha256(konfig2.encode()).hexdigest()
    b2 = befehl(a_id, "konfig", {"text": konfig2.replace("fernwartung = true", "fernwartung = false"), "basis": basis2})
    pi.runde()
    status, text = stand(b2)
    ok("Änderung in [portal] abgelehnt", status == "fehler" and "[portal] kann nur vor Ort" in text, f"{status}: {text}")
    b3 = befehl(a_id, "konfig", {"text": konfig2.replace("Ablauf Neu", "Ablauf Drei"), "basis": basis})
    pi.runde()
    status, text = stand(b3)
    ok("veralteter Stand abgelehnt", status == "fehler" and "inzwischen geändert" in text, f"{status}: {text}")
    ok("Datei nach Ablehnungen unverändert", 'name = "Ablauf Neu"' in pi.pfad.read_text() and pi.neustarts == 1)

    # Update
    b4 = befehl(a_id, "update")
    pi.runde()
    status, text = stand(b4)
    ok("Update: Ergebnis aus dem Protokoll quittiert", status == "erledigt" and text.startswith("OK: abc1234"), f"{status}: {text}")
    pi.update = "fehlt"
    b5 = befehl(a_id, "update")
    pi.runde()
    status, text = stand(b5)
    ok("Update ohne Einrichtung: Fehler mit Text", status == "fehler" and "nicht eingerichtet" in text, f"{status}: {text}")

    # Abregelung aktiv (auch Simulation): abgelehnt
    pi.state.abregelung = type("Abr", (), {"aktiv": True, "modus": "simulation",
                                           "status": lambda self: {
                                               "aktiv": True, "modus": "simulation", "grund": "negativer Preis",
                                               "preis": -5.0, "naechster_negativ": None}})()
    b6 = befehl(a_id, "update")
    pi.runde()
    status, text = stand(b6)
    ok("während Abregelung abgelehnt", status == "fehler" and "abgeregelt" in text, f"{status}: {text}")
    pi.state.abregelung = None

    # Fernwartung auf dem Pi ausschalten (vor Ort): Portal nimmt keine Befehle mehr an, Konfiguration leer
    pi.pfad.write_text(pi.pfad.read_text().replace("fernwartung = true", "fernwartung = false"))
    pi.starten()
    pi.runde()
    ok("ausgeschaltet: Portal zeigt keine Konfiguration mehr",
       sql(f"select text = '' from public.anlagen_konfig where anlage_id = '{a_id}'", "aal2") == "t")
    try:
        befehl(a_id, "update")
        ok("ausgeschaltet: Befehl wird abgelehnt", False)
    except RuntimeError as e:
        ok("ausgeschaltet: Befehl wird abgelehnt", "nicht eingeschaltet" in str(e), str(e))
    ok("keine Quittung blieb liegen", pi.state.fernwartung.z["quittungen"] == [])
    zustand = (pi.d / "portal-befehle.json").read_text()
    ok("kein Geheimnis in der Zustandsdatei", not any(g in zustand for g in (API_KEY, PASSWORT, token)))


def ablauf_alt() -> None:
    a_id, token = anlage("Alte Datenbank", "aal1")
    pi = Pi(token)
    pi.runde()
    pi.runde()
    p = pi.state.portal
    ok("alte Datenbank: Meldung gelingt, kein Fehlerstatus", p.fehler is None and p.letzter_erfolg, str(p.fehler))
    ok("alte Datenbank: Stand ist angekommen",
       sql(f"select version is not null and letzter_kontakt is not null from public.anlagen where id = '{a_id}'") == "t")
    ok("alte Datenbank: Hochladen pausiert", pi.state.fernwartung._konfig_pause_bis > time.time())
    ok("alte Datenbank: nichts ausgeführt", pi.neustarts == 0 and pi.state.fernwartung.z["gesehen"] == [])


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    if "--runde" in sys.argv:
        i = sys.argv.index("--runde")
        pi_ = Pi(sys.argv[i + 2], sys.argv[i + 1])
        pi_.update = "fehlt"
        pi_.runde()
        p_ = pi_.state.portal
        print(json.dumps({"fehler": p_.fehler, "letzter_befehl": p_.letzter_befehl, "neustart": pi_.neustarts > 0}))
        sys.exit(1 if p_.fehler else 0)
    ablauf_alt() if ALT else ablauf_neu()
