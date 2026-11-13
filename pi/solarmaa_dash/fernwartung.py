"""Fernwartung: Befehle des Solarmaa-Portals ausführen (Update, Konfiguration).

Das Portal gibt in der Antwort auf jede Meldung die wartenden Befehle mit. Der Pi prüft jeden
Befehl selbst, führt ihn höchstens einmal aus und meldet das Ergebnis zurück (Quittung).

Grundsätze (docs/ENTSCHEIDE.md E6, E7):
- Nur wenn in der Konfiguration [portal] fernwartung = true steht.
- Nie während einer aktiven Abregelung (gleiche Bedingung wie in der lokalen Wartung).
- Es gibt genau zwei Befehle: «update» und «konfig». Kein Befehl schreibt auf ein Gerät.
- [portal], [server] und [daten] sowie der Demo-Modus sind aus der Ferne nicht änderbar
  (konfig_web.fern_pruefen), damit sich niemand aussperrt.
- Der Zustand steht in <Datenordner>/portal-befehle.json und wird VOR jeder Wirkung geschrieben.
  Quittiert wird erst, wenn das Ergebnis feststeht: bei «konfig» nach dem Neustart mit der neuen
  Fassung, bei «update» aus dem Update-Protokoll. Geht eine Quittung nicht hinaus, bleibt sie in
  der Datei und wird bei jeder Runde neu versucht.
- Startet der Dienst mit einer aus der Ferne gespeicherten Konfiguration nicht, wird die
  vorherige Fassung zurückgespielt (ruecksetzen_falls_probe).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import tomllib
from pathlib import Path

from . import __version__
from . import config as config_mod
from .konfig_web import MASKE, _geheime_werte, _geheimnisse, maskieren, sicher_maskiert

log = logging.getLogger("solarmaa_dash")

DATEI = "portal-befehle.json"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_PAKET = 5                # mehr Befehle in einer Antwort sind ein Fehler des Portals
MAX_PRO_STUNDE = 6
MAX_GESEHEN = 50
MAX_QUITTUNGEN = 20
QUITTUNG_TAGE = 7
FRIST_NEUSTART_S = 5 * 60
FRIST_UPDATE_S = 20 * 60
PAUSE_ALTE_DB_S = 3600
MAX_TEXT = 200_000


def _leer() -> dict:
    return {"gesehen": [], "ausgefuehrt": [], "quittungen": [], "wartend": None}


def _lesen(datei: Path | None) -> dict:
    z = _leer()
    if datei is None or not datei.exists():
        return z
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("Fernwartung: Zustandsdatei nicht lesbar (%s), beginne leer", type(e).__name__)
        return z
    if not isinstance(roh, dict):
        return z
    z["gesehen"] = [x for x in roh.get("gesehen") or [] if isinstance(x, str)][-MAX_GESEHEN:]
    z["ausgefuehrt"] = [x for x in roh.get("ausgefuehrt") or [] if isinstance(x, (int, float))]
    z["quittungen"] = [x for x in roh.get("quittungen") or []
                       if isinstance(x, dict) and isinstance(x.get("id"), str)][-MAX_QUITTUNGEN:]
    w = roh.get("wartend")
    if isinstance(w, dict) and isinstance(w.get("id"), str) and w.get("art") in ("update", "konfig"):
        z["wartend"] = w
    return z


def _schreiben(datei: Path | None, z: dict) -> None:
    if datei is None:
        return
    tmp = datei.with_name(datei.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(z, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, datei)


def _geheime(text: str) -> set[str]:
    """Alle geheimen Werte einer Konfiguration (für das Säubern von Meldungen)."""
    werte = {m.group(2) for _i, _k, m in _geheimnisse(text)}
    try:
        werte |= {str(w) for _p, w in _geheime_werte(tomllib.loads(text))}
    except tomllib.TOMLDecodeError:
        pass
    return {w for w in werte if len(w) >= 4 and w != MASKE}


class Fernwartung:
    def __init__(self, portal, state, daten_dir: Path | None):
        self.portal, self.state = portal, state
        self.datei = (Path(daten_dir) / DATEI) if daten_dir else None
        self.z = _lesen(self.datei)
        # Ein wartender Befehl aus der Datei stammt aus einem früheren Lauf des Dienstes.
        self._vorher = self.z["wartend"]["id"] if self.z["wartend"] else None
        self._hochgeladen: str | None = None
        self._konfig_pause_bis = 0.0
        self._gewarnt: str | None = None
        self._geheim: set[str] = set()

    # ------------------------------------------------------------ Zustand
    def _sichern(self) -> None:
        try:
            _schreiben(self.datei, self.z)
        except OSError as e:
            # Ohne dauerhaften Zustand wäre «höchstens einmal» nicht mehr sicher: lieber nichts ausführen.
            raise RuntimeError(f"Zustand der Fernwartung nicht schreibbar ({type(e).__name__})") from e

    def _saeubern(self, text: str) -> str:
        for w in sorted(self._geheim, key=len, reverse=True):
            text = text.replace(w, MASKE)
        return text[:500]

    def _quittung(self, bid: str, art: str, ok: bool, text: str) -> None:
        text = self._saeubern(str(text))
        self.z["quittungen"] = (self.z["quittungen"] + [{"id": bid, "ok": bool(ok), "text": text,
                                                         "ts": int(time.time())}])[-MAX_QUITTUNGEN:]
        self.portal.letzter_befehl = {"id": bid, "art": art, "ok": bool(ok), "text": text, "ts": int(time.time())}
        log.warning("Fernwartung: %s %s – %s", art, bid, ("ok: " if ok else "abgelehnt/Fehler: ") + text)

    def _konfig_text(self) -> str | None:
        k = getattr(self.state, "konfig", None)
        if k is None or k.pfad is None or not k.pfad.exists():
            return None
        return k.pfad.read_text(encoding="utf-8")

    # ------------------------------------------------------------ Befehle annehmen
    def verarbeiten(self, befehle) -> None:
        if befehle is None or befehle == []:
            return
        if not isinstance(befehle, list):
            log.warning("Fernwartung: Antwort des Portals hat ein unerwartetes Format, verworfen")
            return
        if len(befehle) > MAX_PAKET:
            log.warning("Fernwartung: %d Befehle in einer Antwort, nur die ersten %d werden beachtet",
                        len(befehle), MAX_PAKET)
            befehle = befehle[:MAX_PAKET]
        angenommen = False
        for b in befehle:
            if not (isinstance(b, dict) and isinstance(b.get("id"), str) and UUID.match(b["id"])
                    and isinstance(b.get("art"), str)):
                log.warning("Fernwartung: ungültiger Befehl verworfen")
                continue
            bid, art = b["id"], b["art"][:20]
            if bid in self.z["gesehen"]:
                continue                                   # nie zweimal ausführen
            self.z["gesehen"] = (self.z["gesehen"] + [bid])[-MAX_GESEHEN:]
            self._sichern()                                # vor jeder Wirkung auf die Platte
            alt = self._konfig_text()
            self._geheim = _geheime(alt) if alt else set()
            grund = self._ablehnen(art, angenommen)
            if grund:
                self._quittung(bid, art, False, grund)
                self._sichern()
                continue
            self.z["ausgefuehrt"] = [t for t in self.z["ausgefuehrt"] if t > time.time() - 3600] + [int(time.time())]
            if art == "update":
                angenommen = self._update(bid)
            else:
                angenommen = self._konfig(bid, b.get("nutzlast"))
            self._sichern()

    def _ablehnen(self, art: str, angenommen: bool) -> str | None:
        abr = getattr(self.state, "abregelung", None)
        if not self.portal.fernwartung:
            return "Fernwartung ist auf dieser Anlage ausgeschaltet"
        if abr is not None and abr.aktiv:
            return "Abgelehnt: Anlage wird gerade abgeregelt. Später erneut senden."
        if angenommen:
            return "Ein anderer Befehl wurde gerade angenommen, bitte erneut senden"
        if self.z["wartend"]:
            return "Ein anderer Befehl läuft noch"
        if len([t for t in self.z["ausgefuehrt"] if t > time.time() - 3600]) >= MAX_PRO_STUNDE:
            return "Zu viele Fernbefehle in der letzten Stunde"
        if art not in ("update", "konfig"):
            return "Unbekannter Befehl"
        return None

    def _update(self, bid: str) -> bool:
        self.z["wartend"] = {"id": bid, "art": "update", "ts": time.time()}
        self._sichern()
        try:
            self.state.wartung.update_anfordern("portal")
        except Exception as e:  # noqa: BLE001
            self.z["wartend"] = None
            self._quittung(bid, "update", False, str(e) if isinstance(e, ValueError)
                           else f"Update liess sich nicht anfordern ({type(e).__name__})")
            return False
        log.warning("Fernwartung: update %s – angefordert", bid)
        return True

    def _konfig(self, bid: str, nutzlast) -> bool:
        if not (isinstance(nutzlast, dict) and isinstance(nutzlast.get("text"), str)
                and isinstance(nutzlast.get("basis"), str) and len(nutzlast["text"]) <= MAX_TEXT):
            self._quittung(bid, "konfig", False, "Befehl unvollständig (Text oder Stand fehlt) oder zu gross")
            return False
        text, basis = nutzlast["text"], nutzlast["basis"]
        self._geheim |= _geheime(text)
        self.z["wartend"] = {"id": bid, "art": "konfig", "ts": time.time(), "gespeichert": False,
                             "basis": basis[:64]}
        self._sichern()
        try:
            sicherung = self.state.konfig.speichern(text, "portal", fern=True, basis=basis)
        except Exception as e:  # noqa: BLE001
            self.z["wartend"] = None
            self._quittung(bid, "konfig", False, str(e) if isinstance(e, ValueError)
                           else f"Speichern fehlgeschlagen ({type(e).__name__})")
            return False
        self.z["wartend"].update(gespeichert=True, sicherung=str(sicherung) if sicherung else None)
        self._sichern()
        log.warning("Fernwartung: konfig %s – gespeichert, Dienst startet neu", bid)
        self.state.konfig.neustart()
        return True

    # ------------------------------------------------------------ jede Runde
    def nachlauf(self) -> None:
        self._wartend_auswerten()
        if self.portal.aktiv:
            self._quittungen_senden()
            self._konfig_hochladen()

    def _wartend_auswerten(self) -> None:
        w = self.z["wartend"]
        if not w:
            return
        alter = time.time() - float(w.get("ts") or 0)
        ergebnis: tuple[bool, str] | None = None
        if w["art"] == "konfig":
            if w["id"] == self._vorher:                     # der Dienst ist seither neu gestartet
                if w.get("gespeichert"):
                    ergebnis = True, f"Gespeichert. Dienst läuft mit neuer Konfiguration (Version {__version__})."
                else:
                    alt = self._konfig_text()
                    gleich = alt is not None and \
                        hashlib.sha256(maskieren(alt).encode("utf-8")).hexdigest() == w.get("basis")
                    ergebnis = False, ("Neustart vor dem Speichern, nichts geändert" if gleich else
                                       "Neustart während des Speicherns. Konfiguration im Portal neu laden und prüfen.")
            elif alter > FRIST_NEUSTART_S:
                ergebnis = False, ("Neustart ist ausgeblieben. Die Konfiguration ist gespeichert und gilt "
                                   "ab dem nächsten Neustart des Dienstes.")
        else:
            wartung = getattr(self.state, "wartung", None)
            ergebnis = wartung.update_ergebnis(float(w.get("ts") or 0)) if wartung else None
            if ergebnis is None and alter > FRIST_UPDATE_S:
                ergebnis = False, "Kein Ergebnis im Update-Protokoll"
        if ergebnis is not None:
            self.z["wartend"] = None
            self._quittung(w["id"], w["art"], ergebnis[0], ergebnis[1])
            self._sichern()

    def _quittungen_senden(self) -> None:
        grenze = time.time() - QUITTUNG_TAGE * 86400
        offen = [q for q in self.z["quittungen"] if float(q.get("ts") or 0) > grenze]
        geaendert = len(offen) != len(self.z["quittungen"])
        rest = []
        for i, q in enumerate(offen):
            code, _ = self.portal.rpc("pi_ergebnis", {"p_token": self.portal.token, "p_befehl": q["id"],
                                                      "p_ok": bool(q.get("ok")), "p_text": str(q.get("text", ""))})
            if code in (200, 204):
                geaendert = True
                continue
            rest.append(q)
            if code == 0:                    # Portal nicht erreichbar: Rest beim nächsten Mal
                rest.extend(offen[i + 1:])
                break
        if geaendert:
            self.z["quittungen"] = rest
            self._sichern()

    def _konfig_hochladen(self) -> None:
        if time.time() < self._konfig_pause_bis:
            return
        if self.portal.fernwartung:
            roh = self._konfig_text()
            if roh is None:
                return
            try:
                text = sicher_maskiert(roh)
            except ValueError as e:
                if self._gewarnt != str(e):
                    self._gewarnt = str(e)
                    log.warning("Fernwartung: Konfiguration wird nicht ans Portal übertragen: %s", e)
                self.portal.konfig_hinweis = f"Konfiguration wird nicht ans Portal übertragen: {e}"
                return
            self.portal.konfig_hinweis = None
        else:
            text = ""                        # Portal soll nichts Veraltetes zeigen
        marke = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if marke == self._hochgeladen:
            return
        code, _ = self.portal.rpc("pi_konfig", {"p_token": self.portal.token, "p_text": text})
        if code in (200, 204):
            self._hochgeladen = marke
        elif code == 404:                    # Datenbank des Portals kennt die Fernwartung noch nicht
            self._konfig_pause_bis = time.time() + PAUSE_ALTE_DB_S
            log.info("Portal nimmt noch keine Konfiguration entgegen (ältere Datenbank), nächster Versuch in einer Stunde")


def ruecksetzen_falls_probe(cfg_pfad, fehler: BaseException, daten_dir: Path | None = None) -> bool:
    """Nach einem fehlgeschlagenen Start: Wurde die Konfiguration eben aus der Ferne gespeichert,
    die vorherige Fassung zurückspielen und das als Fehlquittung vormerken. True = zurückgespielt
    (der Aufrufer beendet den Dienst, systemd startet ihn mit der alten Fassung neu)."""
    if not cfg_pfad:
        return False
    try:
        if daten_dir is None:
            pfad = config_mod.DEFAULTS["daten"]["pfad"]
            try:
                with open(cfg_pfad, "rb") as fh:
                    pfad = (tomllib.load(fh).get("daten") or {}).get("pfad") or pfad
            except (OSError, tomllib.TOMLDecodeError, AttributeError):
                pass
            if pfad == ":memory:":
                return False
            daten_dir = Path(pfad).parent
        datei = Path(daten_dir) / DATEI
        z = _lesen(datei)
        w = z["wartend"]
        if not (w and w["art"] == "konfig" and w.get("gespeichert") and w.get("sicherung")):
            return False
        sicherung = Path(w["sicherung"])
        if sicherung.parent != Path(daten_dir) / "config-sicherung" or not sicherung.is_file():
            return False
        with open(cfg_pfad, "w", encoding="utf-8") as fh:      # an Ort und Stelle, wie beim Speichern
            fh.write(sicherung.read_text(encoding="utf-8"))
        z["wartend"] = None
        z["quittungen"] = (z["quittungen"] + [{
            "id": w["id"], "ok": False, "ts": int(time.time()),
            "text": f"Start mit neuer Konfiguration fehlgeschlagen ({type(fehler).__name__}). "
                    "Vorherige Fassung wiederhergestellt."}])[-MAX_QUITTUNGEN:]
        _schreiben(datei, z)
        log.error("Fernwartung: Start mit der aus der Ferne gespeicherten Konfiguration fehlgeschlagen (%s). "
                  "Vorherige Fassung wiederhergestellt.", type(fehler).__name__)
        return True
    except Exception:  # noqa: BLE001 – der ursprüngliche Fehler soll sichtbar bleiben
        log.exception("Fernwartung: Zurückspielen der Konfiguration fehlgeschlagen")
        return False
