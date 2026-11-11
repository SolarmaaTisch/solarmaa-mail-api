"""Alarme: rote Lampe (Shelly oder myStrom), Push (ntfy) und E-Mail (SMTP).

Konfiguration [benachrichtigung]:
  [benachrichtigung.lampe]   typ = "shelly" | "mystrom", host, kanal = 0, passwort
  [benachrichtigung.push]    server = "https://ntfy.sh", thema = "solarmaa-…", token = ""
  [benachrichtigung.email]   smtp_host, smtp_port = 587, sicherheit = "starttls" | "ssl" | "keine",
                             benutzer, passwort, absender, empfaenger = ["…"]
  erinnerung_h = 12          offene Alarme so oft erneut melden

Ein Alarm hat einen Schlüssel (z. B. "zaehler"), einen Text und eine Stufe ("warnung", "kritisch").
Neu → Push + E-Mail, Lampe an. Behoben → Meldung «behoben», Lampe aus, wenn keiner mehr offen ist.
Versand läuft im Hintergrund, damit die Regelung nie wartet.
"""
from __future__ import annotations

import logging
import queue
import smtplib
import ssl
import threading
import time
import urllib.request
from email.message import EmailMessage

from .geraete import GeraetFehler
from .geraete.http import get_raw

log = logging.getLogger("solarmaa_dash")


class Lampe:
    def __init__(self, cfg: dict):
        self.typ = cfg.get("typ", "shelly")
        self.host = cfg["host"]
        self.kanal = int(cfg.get("kanal", 0))
        self.passwort = cfg.get("passwort") or None
        self.gen: int | None = None

    def schalten(self, an: bool) -> None:
        if self.typ == "mystrom":
            get_raw(f"http://{self.host}/relay?state={1 if an else 0}")
            return
        if self.gen is None:
            from .geraete.http import get_json
            self.gen = int(get_json(f"http://{self.host}/shelly", user="admin", password=self.passwort).get("gen", 1) or 1)
        if self.gen >= 2:
            get_raw(f"http://{self.host}/rpc/Switch.Set?id={self.kanal}&on={'true' if an else 'false'}",
                    user="admin", password=self.passwort)
        else:
            get_raw(f"http://{self.host}/relay/{self.kanal}?turn={'on' if an else 'off'}",
                    user="admin", password=self.passwort)


class Alarme:
    def __init__(self, cfg: dict):
        b = cfg.get("benachrichtigung") or {}
        self.anlage = cfg.get("anlage", {}).get("name", "Solaranlage")
        self.lampe = Lampe(b["lampe"]) if (b.get("lampe") or {}).get("host") else None
        self.push = b.get("push") if (b.get("push") or {}).get("thema") else None
        self.email = b.get("email") if (b.get("email") or {}).get("smtp_host") else None
        self.erinnerung_s = float(b.get("erinnerung_h", 12)) * 3600
        self.offen: dict[str, dict] = {}
        self.lampe_an: bool | None = None
        self.lampe_zeit = 0.0
        self.lock = threading.Lock()
        self.q: queue.Queue = queue.Queue()
        self.versandfehler: str | None = None
        threading.Thread(target=self._sender, name="alarme", daemon=True).start()

    # -- öffentliche Schnittstelle ---------------------------------------
    def setzen(self, key: str, text: str, stufe: str = "kritisch") -> None:
        jetzt = time.time()
        with self.lock:
            a = self.offen.get(key)
            if a is None:
                self.offen[key] = {"key": key, "text": text, "stufe": stufe, "seit": jetzt, "gemeldet": jetzt}
                self.q.put(("neu", key, text, stufe))
                log.warning("ALARM %s: %s", key, text)
            else:
                a["text"] = text
                if jetzt - a["gemeldet"] >= self.erinnerung_s:
                    a["gemeldet"] = jetzt
                    self.q.put(("erinnerung", key, text, a["stufe"]))
        self._lampe_pruefen()

    def loeschen(self, key: str) -> None:
        with self.lock:
            a = self.offen.pop(key, None)
        if a:
            dauer = int((time.time() - a["seit"]) / 60)
            self.q.put(("behoben", key, f"{a['text']} (Dauer {dauer} min)", a["stufe"]))
            log.info("Alarm behoben %s", key)
        self._lampe_pruefen()

    def test(self) -> None:
        self.q.put(("test", "test", "Testmeldung: Benachrichtigung funktioniert.", "warnung"))
        if self.lampe:
            self.q.put(("lampe_blinken", "", "", ""))

    def status(self) -> dict:
        with self.lock:
            offen = sorted(self.offen.values(), key=lambda a: a["seit"])
        return {"offen": offen, "lampe": self.lampe is not None, "push": self.push is not None,
                "email": self.email is not None, "versandfehler": self.versandfehler}

    # -- intern ------------------------------------------------------------
    def _lampe_pruefen(self) -> None:
        if not self.lampe:
            return
        with self.lock:
            soll = any(a["stufe"] == "kritisch" for a in self.offen.values())
        # Zustand regelmässig erneut setzen (falls die Lampe neu gestartet wurde)
        if soll != self.lampe_an or time.time() - self.lampe_zeit > 300:
            self.lampe_an, self.lampe_zeit = soll, time.time()
            self.q.put(("lampe", "", "an" if soll else "aus", ""))

    def _sender(self) -> None:
        while True:
            art, key, text, stufe = self.q.get()
            try:
                if art == "lampe":
                    self.lampe.schalten(text == "an")
                elif art == "lampe_blinken":
                    self.lampe.schalten(True)
                    time.sleep(5)
                    self.lampe.schalten(bool(self.lampe_an))
                else:
                    titel = {"neu": "Störung", "erinnerung": "Störung besteht weiter",
                             "behoben": "Behoben", "test": "Test"}[art]
                    self._senden(f"{self.anlage}: {titel}", text, stufe if art != "behoben" else "info")
                self.versandfehler = None
            except Exception as e:  # noqa: BLE001 – der Sender darf nie sterben
                self.versandfehler = f"{art}: {e}"
                log.warning("Benachrichtigung fehlgeschlagen (%s): %s", art, e)

    def _senden(self, titel: str, text: str, stufe: str) -> None:
        fehler = []
        if self.push:
            try:
                server = self.push.get("server", "https://ntfy.sh").rstrip("/")
                hdr = {"Title": titel,
                       "Priority": {"kritisch": "5", "warnung": "4"}.get(stufe, "3"),
                       "Tags": {"kritisch": "rotating_light", "warnung": "warning"}.get(stufe, "white_check_mark")}
                if self.push.get("token"):
                    hdr["Authorization"] = f"Bearer {self.push['token']}"
                req = urllib.request.Request(f"{server}/{self.push['thema']}", data=text.encode("utf-8"),
                                             method="POST", headers={k: _hdr(v) for k, v in hdr.items()})
                with urllib.request.urlopen(req, timeout=10):
                    pass
            except Exception as e:  # noqa: BLE001
                fehler.append(f"Push: {e}")
        if self.email:
            try:
                m = EmailMessage()
                m["Subject"], m["From"] = titel, self.email.get("absender") or self.email.get("benutzer")
                m["To"] = ", ".join(self.email["empfaenger"])
                m.set_content(f"{text}\n\n– Solarmaa Energie-Dashboard, {time.strftime('%d.%m.%Y %H:%M')}")
                port = int(self.email.get("smtp_port", 587))
                sicherheit = self.email.get("sicherheit", "starttls")
                if sicherheit == "ssl":
                    s = smtplib.SMTP_SSL(self.email["smtp_host"], port, timeout=15, context=ssl.create_default_context())
                else:
                    s = smtplib.SMTP(self.email["smtp_host"], port, timeout=15)
                    if sicherheit == "starttls":
                        s.starttls(context=ssl.create_default_context())
                with s:
                    if self.email.get("benutzer"):
                        s.login(self.email["benutzer"], self.email.get("passwort", ""))
                    s.send_message(m)
            except Exception as e:  # noqa: BLE001
                fehler.append(f"E-Mail: {e}")
        if fehler:
            raise OSError("; ".join(fehler))


def _hdr(v: str) -> str:
    """HTTP-Header nur mit Latin-1; Umlaute ersetzen, damit ntfy-Titel ankommen."""
    return v.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
                                      "–": "-", "«": '"', "»": '"'})).encode("latin-1", "ignore").decode("latin-1")


class KeineAlarme:
    """Platzhalter, wenn nichts konfiguriert ist (Alarme werden nur geloggt)."""

    def __init__(self):
        self.offen: dict = {}

    def setzen(self, key, text, stufe="kritisch"):
        if key not in self.offen:
            log.warning("ALARM %s: %s", key, text)
        self.offen[key] = {"key": key, "text": text, "stufe": stufe, "seit": time.time()}

    def loeschen(self, key):
        self.offen.pop(key, None)

    def test(self):
        pass

    def status(self):
        return {"offen": list(self.offen.values()), "lampe": False, "push": False, "email": False,
                "versandfehler": None}
