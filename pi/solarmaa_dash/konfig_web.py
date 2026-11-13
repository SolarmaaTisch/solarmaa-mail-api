"""Konfiguration im Browser bearbeiten (nur Rolle admin).

- Geheime Werte (Passwörter, Tokens, API-Keys) werden beim Anzeigen durch ******** ersetzt und
  beim Speichern wieder eingesetzt, wenn sie unverändert sind. So wandern sie nicht unnötig
  durchs Netz.
- Vor dem Speichern wird die neue Konfiguration vollständig geprüft (Geräte, Haus, Abregelung).
  Fehler werden angezeigt, gespeichert wird nur eine gültige Datei.
- Die vorherige Fassung wird gesichert (die letzten 10 unter <Datenordner>/config-sicherung).
- Danach startet der Dienst neu (systemd), damit die Änderung gilt.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import signal
import tempfile
import threading
import time
import tomllib
from pathlib import Path

from . import config as config_mod

MASKE = "********"
# thema (ntfy) und lebenszeichen_url wirken wie Passwörter: Wer sie kennt, kann mitlesen bzw. falsch melden.
GEHEIM_NAMEN = ("passwort", "api_key", "token", "entsoe_token", "thema", "lebenszeichen_url")
GEHEIM = re.compile(r'^(\s*(?:' + "|".join(GEHEIM_NAMEN) + r')\s*=\s*)"([^"]*)"(.*)$')
# Nur vor Ort änderbar (ENTSCHEIDE E7): Sonst könnte sich Solarmaa selbst aussperren.
GESPERRT_FERN = ("portal", "server", "daten")
# Mehrzeilige Zeichenketten können ganze nachgebaute Abschnitte enthalten, die die zeilenweise
# Erkennung der Geheimnisse täuschen: über das Portal nicht erlaubt.
MEHRZEILIG = ("'''", '"""')
TABELLE = re.compile(r"^\s*\[\[?[^\]]+\]\]?\s*(#.*)?$")


def _geheimnisse(text: str):
    """Liefert (zeilenindex, schluessel, wert) für jede geheime Zeile. Schlüssel = Abschnitt + Nr. + Name."""
    zaehler: dict[str, int] = {}
    abschnitt = ""
    for i, z in enumerate(text.split("\n")):
        if TABELLE.match(z):
            kopf = z.split("#")[0].strip()
            zaehler[kopf] = zaehler.get(kopf, 0) + 1
            abschnitt = f"{kopf}#{zaehler[kopf]}"
            continue
        m = GEHEIM.match(z)
        if m:
            yield i, f"{abschnitt}|{m.group(1).split('=')[0].strip()}", m


def maskieren(text: str) -> str:
    zeilen = text.split("\n")
    for i, _k, m in _geheimnisse(text):
        if m.group(2):
            zeilen[i] = f'{m.group(1)}"{MASKE}"{m.group(3)}'
    return "\n".join(zeilen)


def demaskieren(neu: str, alt: str) -> str:
    alte = {k: m.group(2) for _i, k, m in _geheimnisse(alt)}
    zeilen = neu.split("\n")
    for i, k, m in _geheimnisse(neu):
        if m.group(2) == MASKE:
            if k not in alte:
                raise ValueError(f"Zeile {i + 1}: Platzhalter {MASKE} ohne gespeicherten Wert. Bitte den echten Wert eintragen. "
                                 "Wurden Einträge eingefügt, verschoben oder gelöscht, sind die Geheimnisse der "
                                 "nachfolgenden Einträge neu einzugeben.")
            zeilen[i] = f'{m.group(1)}"{alte[k]}"{m.group(3)}'
    return "\n".join(zeilen)


def _geheime_werte(knoten, pfad: str = ""):
    """Liefert (pfad, wert) für jeden Wert eines geheimen Schlüssels in einer geladenen Konfiguration."""
    if isinstance(knoten, dict):
        for k, v in knoten.items():
            if k in GEHEIM_NAMEN and not isinstance(v, (dict, list)):
                yield f"{pfad}{k}", v
            else:
                yield from _geheime_werte(v, f"{pfad}{k}.")
    elif isinstance(knoten, list):
        for i, v in enumerate(knoten):
            yield from _geheime_werte(v, f"{pfad}{i}.")


def sicher_maskiert(text: str) -> str:
    """Maskiert wie maskieren() und prüft das Ergebnis nach: Die Zeilenerkennung findet nur
    schluessel = "…". Steht ein Geheimnis anders in der Datei ('…', mehrzeilig, in einer Zeile mit
    anderem), bliebe es stehen. Dann lieber gar nichts herausgeben (ValueError)."""
    maskiert = maskieren(text)
    try:
        original, ergebnis = tomllib.loads(text), tomllib.loads(maskiert)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"Konfiguration nicht lesbar: {e}") from e
    for pfad, wert in _geheime_werte(ergebnis):
        if wert not in ("", MASKE):
            raise ValueError(f"Geheimer Wert {pfad} steht in einer Form, die nicht verdeckt werden kann. "
                             'Bitte als schluessel = "wert" auf einer eigenen Zeile schreiben.')
    for pfad, wert in _geheime_werte(original):
        if isinstance(wert, str) and len(wert) >= 8 and wert in maskiert:
            raise ValueError(f"Geheimer Wert {pfad} kommt an anderer Stelle der Konfiguration nochmals vor.")
    return maskiert


def _blaetter(knoten, pfad: tuple = ()):
    """Liefert (pfad, wert) für jeden einfachen Wert einer geladenen Konfiguration."""
    if isinstance(knoten, dict):
        for k, v in knoten.items():
            yield from _blaetter(v, pfad + (k,))
    elif isinstance(knoten, list):
        for i, v in enumerate(knoten):
            yield from _blaetter(v, pfad + (i,))
    else:
        yield pfad, knoten


def _bei(knoten, pfad: tuple):
    for k in pfad:
        try:
            knoten = knoten[k]
        except (KeyError, IndexError, TypeError):
            return None
    return knoten


def _pfad_text(pfad: tuple) -> str:
    """('geraet', 1) -> «[[geraet]] Nr. 2», ('smartme',) -> «[smartme]», () -> «Dateianfang»."""
    if not pfad:
        return "Dateianfang"
    teile, nr = [], ""
    for k in pfad:
        if isinstance(k, int):
            nr = f" Nr. {k + 1}"
        else:
            teile.append(k)
            nr = ""
    name = ".".join(teile)
    return f"[[{name}]]{nr}" if nr else f"[{name}]"


def _einfach(tabelle) -> dict:
    """Die Felder eines Tabelleneintrags ohne Geheimnisse und ohne Untertabellen (host, basis_url, benutzer …)."""
    if not isinstance(tabelle, dict):
        return {}
    return {k: v for k, v in tabelle.items() if k not in GEHEIM_NAMEN and not isinstance(v, dict)
            and not (isinstance(v, list) and any(isinstance(x, dict) for x in v))}


def uebernahme_pruefen(gesendet: str, neu: str, alt: str) -> None:
    """Prüft auf TOML-Ebene, was demaskieren() eingesetzt hat. gesendet = Text aus dem Portal
    (Geheimnisse evtl. als ********), neu = derselbe Text nach demaskieren(), alt = Datei auf dem Pi.

    Ein gespeichertes Geheimnis darf nur dorthin zurück, wo es herkommt: unter denselben Pfad, und
    nur, wenn die übrigen Felder dieses Eintrags (host, basis_url, benutzer …) unverändert sind.
    Sonst liesse sich das echte Passwort an eine fremde Adresse schicken oder in ein Feld setzen,
    das nicht als geheim gilt und darum im Portal lesbar wäre."""
    if any(m in gesendet for m in MEHRZEILIG):
        raise ValueError("Mehrzeilige Zeichenketten (''' oder \"\"\") sind über das Portal nicht erlaubt")
    try:
        t_ges, t_neu, t_alt = tomllib.loads(gesendet), tomllib.loads(neu), tomllib.loads(alt)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"Konfiguration ungültig, nichts gespeichert: {e}") from e
    b_ges, b_neu = dict(_blaetter(t_ges)), dict(_blaetter(t_neu))
    if b_ges.keys() != b_neu.keys():
        raise ValueError(f"Platzhalter {MASKE} steht an einer Stelle, an der kein Geheimnis erwartet wird")
    for pfad, wert in b_neu.items():
        if wert == b_ges[pfad] and type(wert) is type(b_ges[pfad]):
            continue                                   # so gesendet, nichts eingesetzt
        eintrag, name = pfad[:-1], pfad[-1]
        wo = _pfad_text(eintrag)
        if name not in GEHEIM_NAMEN or b_ges[pfad] != MASKE:
            raise ValueError(f"Platzhalter {MASKE} steht in {wo} an einer Stelle, an der kein Geheimnis erwartet wird")
        if _bei(t_alt, pfad) != wert:
            raise ValueError(f"{wo}: Für {name} ist an dieser Stelle kein Wert gespeichert. Wurden Einträge "
                             f"verschoben, eingefügt oder gelöscht, das Geheimnis neu eingeben (statt {MASKE}).")
        if _einfach(_bei(t_neu, eintrag)) != _einfach(_bei(t_alt, eintrag)):
            raise ValueError(f"Abschnitt {wo} wurde geändert; Geheimnis neu eingeben "
                             f"(statt {MASKE} den echten Wert eintragen). Das gilt auch, wenn Einträge verschoben wurden.")


def fern_pruefen(cfg_neu: dict, cfg_alt: dict) -> None:
    """Regeln für Änderungen über das Portal (ENTSCHEIDE E7), auf den geladenen Konfigurationen."""
    from .sources import ist_demo
    for k in GESPERRT_FERN:
        if cfg_neu.get(k) != cfg_alt.get(k):
            raise ValueError(f"Abschnitt [{k}] kann nur vor Ort geändert werden")
    # Massgebend ist die Quelle, die tatsächlich laufen würde (demo = typ demo UND keine Geräte).
    if (ist_demo(cfg_neu) and not ist_demo(cfg_alt)) or \
            (cfg_neu["quelle"].get("typ") == "demo" and cfg_alt["quelle"].get("typ") != "demo"):
        raise ValueError("Der Demo-Modus (quelle.typ = \"demo\" ohne Geräte) kann nur vor Ort eingeschaltet werden")
    # Pfad einer lokalen Datei: Aus der Ferne gesetzt, liesse sich damit eine beliebige Datei des Pi einlesen.
    if cfg_neu["abregelung"].get("csv_pfad", "") != cfg_alt["abregelung"].get("csv_pfad", ""):
        raise ValueError("csv_pfad kann nur vor Ort geändert werden")
    if cfg_neu["haus"].get("demo") and not cfg_alt["haus"].get("demo"):
        raise ValueError("Der Demo-Modus ([haus] demo) kann nur vor Ort eingeschaltet werden")


def neue_abschnitte_pruefen(neu: str, alt: str) -> None:
    """Lehnt oberste Abschnitte ab, die das Programm nicht kennt und die es bisher in der Datei
    nicht gab. (Bestehende unbekannte Abschnitte bleiben erlaubt, damit keine laufende Anlage
    plötzlich nicht mehr speicherbar ist.)"""
    try:
        bisher = set(tomllib.loads(alt))
    except tomllib.TOMLDecodeError:
        bisher = set()
    try:
        fremd = sorted(set(tomllib.loads(neu)) - set(config_mod.DEFAULTS) - bisher)
    except tomllib.TOMLDecodeError:
        return                       # die eigentliche Prüfung meldet den Fehler mit Zeile
    if fremd:
        raise ValueError("Konfiguration ungültig, nichts gespeichert: unbekannter Abschnitt " +
                         ", ".join(f"[{k}]" for k in fremd))


def pruefen(text: str, streng: bool = True) -> dict:
    """Lädt die Konfiguration probeweise und baut alle Teile auf, ohne Geräte anzusprechen.
    streng: auch Werte ablehnen, die eine bestehende Datei beim Start nur als Warnung meldet."""
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        tmp = fh.name
    try:
        cfg = config_mod.load(tmp, streng=streng)
    finally:
        os.unlink(tmp)
    probe = copy.deepcopy(cfg)
    probe["daten"]["pfad"] = ":memory:"
    from .sources import make_source
    from .store import Store
    make_source(probe)
    store = Store(":memory:")
    if probe["haus"].get("aktiv"):
        from .haus import Haus
        Haus(probe, store)
    if probe["abregelung"].get("modus", "aus") != "aus" or probe["abregelung"].get("wechselrichter"):
        from .abregelung import Abregelung
        from .preise import Preise
        Abregelung(probe, Preise(probe["abregelung"], None), store)
    from .portal import Portal
    Portal(probe)
    return cfg


class KonfigWeb:
    def __init__(self, pfad: str | None, daten_dir: Path | None):
        self.pfad = Path(pfad) if pfad else None
        self.sicherung = (daten_dir / "config-sicherung") if daten_dir else None
        self.lock = threading.Lock()

    def lesen(self) -> dict:
        if self.pfad is None or not self.pfad.exists():
            raise ValueError("Der Dienst läuft ohne Konfigurationsdatei (Demo). Bearbeiten ist hier nicht möglich.")
        return {"text": maskieren(self.pfad.read_text(encoding="utf-8")), "pfad": str(self.pfad),
                "schreibbar": os.access(self.pfad, os.W_OK)}

    def speichern(self, text: str, von: str, fern: bool = False, basis: str | None = None) -> Path | None:
        """Prüft und speichert. Liefert den Pfad der Sicherung der vorherigen Fassung (oder None).
        fern = True: Änderung über das Portal; basis = SHA-256 der maskierten Fassung, die dort
        bearbeitet wurde. Dann gelten zusätzlich die Regeln aus fern_pruefen()."""
        with self.lock:              # lokale Wartung und Portal-Verbindung schreiben nie gleichzeitig
            return self._speichern(text, von, fern, basis)

    def _speichern(self, text: str, von: str, fern: bool, basis: str | None) -> Path | None:
        if self.pfad is None:
            raise ValueError("Keine Konfigurationsdatei vorhanden")
        if not os.access(self.pfad, os.W_OK):
            raise ValueError("Die Konfigurationsdatei ist für den Dienst nicht schreibbar. "
                             "Einmal «Update installieren» ausführen, dann erneut versuchen.")
        if len(text) > 200_000:
            raise ValueError("Konfiguration zu gross")
        alt = self.pfad.read_text(encoding="utf-8")
        if fern and basis != hashlib.sha256(maskieren(alt).encode("utf-8")).hexdigest():
            raise ValueError("Konfiguration wurde inzwischen geändert. Im Portal neu laden.")
        text = text.replace("\r\n", "\n")
        if fern and any(m in text for m in MEHRZEILIG):
            raise ValueError("Mehrzeilige Zeichenketten (''' oder \"\"\") sind über das Portal nicht erlaubt")
        neu = demaskieren(text, alt)
        neue_abschnitte_pruefen(neu, alt)
        try:
            cfg_neu = pruefen(neu)
        except Exception as e:  # noqa: BLE001 – jede Ursache dem Benutzer zeigen
            raise ValueError(f"Konfiguration ungültig, nichts gespeichert: {e}") from e
        if fern:
            try:
                cfg_alt = pruefen(alt, streng=False)     # die bisherige Datei darf alte Mängel haben
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"Bisherige Konfiguration nicht prüfbar, nichts gespeichert: {e}") from e
            fern_pruefen(cfg_neu, cfg_alt)
            uebernahme_pruefen(text, neu, alt)
        ziel = None
        if self.sicherung is not None:
            self.sicherung.mkdir(mode=0o750, exist_ok=True)
            ziel = self.sicherung / f"config-{time.strftime('%Y%m%d-%H%M%S')}-{von}.toml"
            ziel.write_text(alt, encoding="utf-8")
            for alt_datei in sorted(self.sicherung.glob("config-*.toml"))[:-10]:
                alt_datei.unlink()
        with open(self.pfad, "w", encoding="utf-8") as fh:   # an Ort und Stelle (Datei gehört root:Dienst 660)
            fh.write(neu)
        return ziel

    @staticmethod
    def neustart(verzoegerung_s: float = 1.5) -> None:
        """Dienst sauber beenden; systemd (Restart=always) startet ihn mit der neuen Konfiguration."""
        threading.Timer(verzoegerung_s, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
